"""E2E test fixtures.

Inherits the `isolated_root` autouse fixture from `tests/conftest.py:15-21`
via pytest's directory-walk discovery. Adds E2E-specific fixtures:

- `app_with_db` — full FastAPI app wired to an in-memory sqlite engine
  matching production schema (Base.metadata.create_all). Mirrors the
  posture documented in `tests/test_r3_cross_org_isolation.py:13-17`
  (sqlite for ORM-portable logic; Postgres-parity tests deferred to a
  Neon test branch token in D5+).
- `async_client` — `httpx.AsyncClient` against the ASGI app (in-process,
  no uvicorn).
- `moto_r2` — moto-backed S3 fake serving as the R2 stand-in. The
  endpoint URL is patched into `app.storage.r2._client()` via monkeypatch.
- `clerk_jwt_mint` — local RSA keypair + JWKS server fixture for minting
  test JWTs that pass the D4 Clerk middleware (per
  `dispatches/D4_specs.md` §4 Story #2 + `coverage_expansion.md` §4.2).
- `stripe_signed_event` — helper that produces a `(payload_bytes,
  signature_header)` tuple using `stripe.Webhook.construct_event`'s
  signing primitive (test side only). Spec §3.2 of
  `app/api/stripe_webhook_spec.md`.

All fixtures here are STUBS until D4 ships the surfaces they exercise.
"""

from __future__ import annotations

# IMPLEMENT IN D5 — fixtures depend on D4-shipped surfaces:
#   - Clerk JWT middleware (backend D4 Story #2)
#   - app/api/billing.py routes (backend D4 Story #6, #7)
#   - JWT-aware refactor of upload.py/status.py/download.py (backend D4 Refactor #3)
