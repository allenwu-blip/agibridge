"""R2 storage boundary / edge-case tests — Cycle N coverage expansion.

The 4 scenarios in `tests/test_r3_cross_org_isolation.py:108-145` cover
the *security* surface (prefix-mismatch refusal). This file covers the
*correctness* surface around R2 key construction, TTL behavior, and edge
sizes that the R3 tests do not exercise.

Source: `app/storage/r2.py`. Public API:
- `input_key(org_id, job_id, filename)` :62-64
- `output_key(org_id, job_id)` :67-69
- `presign_upload(...)` :72-89
- `presign_download(...)` :92-117
- TTL constants `UPLOAD_TTL_S = 900`, `DOWNLOAD_TTL_S = 300` :22-24

Test infra posture: tests that exercise pure key-derivation logic
(`input_key`, `output_key`, prefix checks) run without network. Tests
that exercise `boto3.client.generate_presigned_url` are marked
`# IMPLEMENT IN D4 — needs moto` because moto's S3 mock supports
presigning. We do NOT hit live R2 from CI per the brief's hard constraint
"recommend NONE real-vendor in CI".
"""

from __future__ import annotations

import uuid

import pytest

from app.storage import r2

# ----------------------- key derivation: pure-Python boundary cases -----------------------


def test_input_key_with_zero_length_filename_does_not_collapse_prefix():
    """Edge case: filename = "" → `_sanitize_filename` returns "". The
    resulting key MUST still have the full `orgs/{org}/jobs/{job}/input/`
    prefix; only the leaf is empty.

    Source: `app/storage/r2.py:55-64`. _sanitize_filename runs string
    replacements only; never mutates length below 0. The R3 invariant
    is the *prefix*; an empty leaf is a malformed-upload symptom that
    should fail at the boto3 layer with a clean error, not silently
    write to `orgs/{org}/jobs/{job}/input` (which would shadow the dir).
    """
    job_id = uuid.uuid4()
    key = r2.input_key("org_AAA", job_id, "")
    expected_prefix = f"orgs/org_AAA/jobs/{job_id}/input/"
    assert key.startswith(expected_prefix), f"prefix collapsed on empty filename: {key!r}"
    # Leaf is empty after the slash.
    assert key == expected_prefix + "", f"unexpected leaf: {key!r}"


def test_input_key_with_unicode_filename_preserves_bytes():
    """Filename with non-ASCII bytes (e.g. CJK characters in archive name)
    MUST be preserved verbatim through `_sanitize_filename` since that
    helper only strips `/`, `\\`, `..`.

    Justification: AgiBot World ships archive names like `任务_675.zip`
    in some user-uploaded datasets. S3 keys allow UTF-8 per
    https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html
    (accessed 2026-05-14: "safe characters" + "characters that might
    require special handling" both allow UTF-8). Test pins that we do
    not accidentally ASCII-strip.
    """
    job_id = uuid.uuid4()
    unicode_name = "任务_675.zip"
    key = r2.input_key("org_AAA", job_id, unicode_name)
    assert key.endswith(unicode_name), f"unicode bytes lost: {key!r}"


def test_input_key_with_backslash_traversal_attempt_sanitized():
    """Windows-style path traversal attempt `..\\..\\evil.zip` MUST be
    sanitized.

    Source: `app/storage/r2.py:55-59` replaces `\\` → `_` and `..` → `_`.
    The R3 brief specifically calls out prefix-injection attempts.
    """
    job_id = uuid.uuid4()
    key = r2.input_key("org_AAA", job_id, "..\\..\\evil.zip")
    expected_prefix = f"orgs/org_AAA/jobs/{job_id}/input/"
    assert key.startswith(expected_prefix)
    leaf = key[len(expected_prefix) :]
    assert "\\" not in leaf, f"backslash survived sanitization: {leaf!r}"
    assert ".." not in leaf, f"parent-ref survived sanitization: {leaf!r}"


def test_output_key_is_deterministic_per_org_job():
    """`output_key(org_id, job_id)` must be a pure function — same args,
    same key. The download flow re-derives the expected prefix from
    `(org_id, job_id)` in `presign_download` (`r2.py:108`); a
    non-deterministic output_key would break that defense-in-depth.

    Source: `app/storage/r2.py:67-69`.
    """
    job_id = uuid.uuid4()
    k1 = r2.output_key("org_AAA", job_id)
    k2 = r2.output_key("org_AAA", job_id)
    assert k1 == k2
    assert k1 == f"orgs/org_AAA/jobs/{job_id}/output/result.zip"


def test_output_key_differs_across_jobs_within_same_org():
    """Two distinct jobs in the same org MUST get distinct output keys —
    otherwise the second conversion overwrites the first's result.zip in
    R2, and a stale presigned URL would serve the wrong dataset.
    """
    j1 = uuid.uuid4()
    j2 = uuid.uuid4()
    assert j1 != j2
    assert r2.output_key("org_AAA", j1) != r2.output_key("org_AAA", j2)


# ----------------------- TTL boundary -----------------------


def test_upload_ttl_is_exactly_15_minutes():
    """Spec / source pin: `UPLOAD_TTL_S = 15 * 60` at r2.py:23.

    Rationale documented in source: "covers slow uplinks for the 800 MB
    cap (upload.py:30)". Catches a refactor that silently shrinks this
    to seconds (would 403 mid-upload) or grows it to hours (extends the
    leak window beyond what the security review approved).
    """
    assert r2.UPLOAD_TTL_S == 900


def test_download_ttl_is_exactly_5_minutes():
    """Spec / source pin: `DOWNLOAD_TTL_S = 5 * 60` at r2.py:24.

    Rationale documented in source: "keeps a leaked URL short-lived".
    Same regression-pin posture as the upload TTL test.
    """
    assert r2.DOWNLOAD_TTL_S == 300


# ----------------------- presigned URL with moto -----------------------


@pytest.mark.asyncio
async def test_presigned_upload_url_just_before_expiry_still_signs():
    """Boundary: presign generation just before TTL exhaustion still
    yields a signable URL.

    Per AWS SigV4 (R2's `signature_version="s3v4"` at r2.py:47), the
    URL embeds `X-Amz-Date` and `X-Amz-Expires` and is valid for the
    Expires window from sign-time. We can't test "the URL works AT
    expiry+0s" because that's the server's clock judgment; we CAN test
    that we produce a SigV4 URL with the expected Expires param.

    Implementation note: use `moto` (the AWS SDK mock — pure-Python, no
    network) per the brief's "fakes" recommendation. Assert the returned
    URL contains `X-Amz-Expires=900`.
    """
    # IMPLEMENT IN D4 — needs `moto>=5.0` in test deps; pin via pyproject.toml dev-extra


@pytest.mark.asyncio
async def test_presigned_download_url_format_is_sigv4():
    """Per r2.py:47 (`signature_version="s3v4"`), the returned URL must
    be SigV4-shaped: query-param signature, X-Amz-Date, X-Amz-Algorithm.

    Mock setup mirrors the upload test (moto S3 backend). Assert URL
    matches `r'\\?.*X-Amz-Algorithm=AWS4-HMAC-SHA256'`.
    """
    # IMPLEMENT IN D4 — needs moto; same fixture as above


def test_presign_download_malformed_key_with_only_prefix_marker_refused():
    """Subtle prefix-bypass: stored key is literally
    `orgs/org_AAA/jobs/<job-id>/` (trailing slash, NO 'output' segment).
    The startswith check `r2.py:108-112` would pass — caller might then
    sign a directory-listing URL.

    Source: prefix check uses `startswith(expected_prefix)` where
    `expected_prefix = f"orgs/{org_id}/jobs/{job_id}/"`. The empty-suffix
    case is permitted by startswith. Defense-in-depth check needed: the
    stored key should additionally end in a recognized object (e.g.
    `output/result.zip`). This test pins the GAP — failing under current
    code is the intended signal for D4 to harden.
    """
    # IMPLEMENT IN D4 — see app/storage/r2.py:108-112 hardening; gap surfaced
    # in coverage_expansion.md §2 R2 boundary subsection
