"""Cloudflare R2 client (S3-compatible).

Endpoint format: https://<ACCOUNT_ID>.r2.cloudflarestorage.com
Source: https://developers.cloudflare.com/r2/api/s3/api/ (accessed 2026-05-13)

R3 key-prefix discipline: `orgs/{org_id}/jobs/{job_id}/{input,output}/`. The
`org_id` MUST come from the verified JWT claim, NEVER from the request body.
Defense-in-depth: `presign_download` re-derives the expected prefix and
refuses to sign on mismatch.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache

import boto3
from botocore.client import BaseClient, Config

# Presign TTLs. Upload 15 min covers slow uplinks for the 800 MB cap
# (upload.py:30); download 5 min keeps a leaked URL short-lived.
UPLOAD_TTL_S = 15 * 60
DOWNLOAD_TTL_S = 5 * 60


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} env var not set")
    return val


@lru_cache(maxsize=1)
def _client() -> BaseClient:
    """Lazily-built boto3 client. lru_cache ensures one TCP-pool per process.

    Cache invalidation is process restart; for credential rotation we redeploy
    (DevOps Automator owns the rotation cron).
    """
    account_id = _required("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=_required("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_required("R2_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", region_name="auto"),
    )


def _bucket() -> str:
    return _required("R2_BUCKET")


def _sanitize_filename(filename: str) -> str:
    """Strip path-traversal sequences. S3 bucket prefix is the security
    boundary; this is a usability backstop so downstream tools don't choke.
    """
    return filename.replace("/", "_").replace("\\", "_").replace("..", "_")


def input_key(org_id: str, job_id: uuid.UUID, filename: str) -> str:
    """Canonical key for an uploaded archive. ALWAYS org-prefixed."""
    return f"orgs/{org_id}/jobs/{job_id}/input/{_sanitize_filename(filename)}"


def output_key(org_id: str, job_id: uuid.UUID) -> str:
    """Canonical key for a converted output zip."""
    return f"orgs/{org_id}/jobs/{job_id}/output/result.zip"


def presign_upload(
    *,
    org_id: str,
    job_id: uuid.UUID,
    filename: str,
) -> dict[str, str | int]:
    """Return `{url, key, expires_in}` for a PUT-upload URL.

    Caller MUST pass `org_id` from JWT, not from request body. `filename` is
    untrusted; we sanitize but the bucket prefix is the actual boundary.
    """
    key = input_key(org_id, job_id, filename)
    url = _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=UPLOAD_TTL_S,
    )
    return {"url": url, "key": key, "expires_in": UPLOAD_TTL_S}


def presign_download(
    *,
    org_id: str,
    job_id: uuid.UUID,
    output_key_value: str,
) -> str:
    """Return a GET-presigned URL for a finished job's output.

    `output_key_value` is what's stored in `jobs.output_uri`. We re-derive the
    expected prefix `orgs/{org_id}/jobs/{job_id}/` and refuse to sign on
    mismatch — defense in depth against a corrupted row or bug elsewhere.

    Raises:
        PermissionError: if the stored key's prefix doesn't match
            (org_id, job_id). Caller maps to HTTP 403/404 as appropriate.
    """
    expected_prefix = f"orgs/{org_id}/jobs/{job_id}/"
    if not output_key_value.startswith(expected_prefix):
        raise PermissionError(
            f"refuse to sign: output_key prefix mismatch (expected {expected_prefix!r})"
        )
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": output_key_value},
        ExpiresIn=DOWNLOAD_TTL_S,
    )
