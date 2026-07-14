"""Stripe + tier-gating configuration.

Env-var access follows the SAME lazy `os.environ` pattern as
`app/storage/r2.py:27-31` and `app/api/clerk_auth.py:149` — no
pydantic-settings, because the rest of the codebase reads env directly and
lazily so import-time / test collection never needs the vars set. A
`settings`-style object is exposed so call-sites read
`settings.STRIPE_WEBHOOK_SECRET` exactly as `stripe_webhook_spec.md:203`
prescribes.

EXACT env var names (orchestrator-provisioned ground truth, DR-020 lesson —
build to these verbatim, do not invent):
  STRIPE_SECRET_KEY            sk_test_...   (Stripe API auth)
  STRIPE_PUBLISHABLE_KEY       pk_test_...   (frontend; surfaced via /pricing)
  STRIPE_WEBHOOK_SECRET        whsec_...     (signature verification, §3.5)
  STRIPE_PRICE_SOLO            price_...     ($50/mo  -> OrgTier.solo)
  STRIPE_PRICE_TEAM            price_...     ($250/mo -> OrgTier.team)
  STRIPE_PRICE_ENTERPRISE      price_...     ($1000/mo-> OrgTier.enterprise)

Soft-cap ladder is `dispatches/D4_specs.md:37` (Cycle J ratified):
Free 5 / Solo 50 / Team 200 / Enterprise unlimited monthly conversion jobs.
This is the authoritative ladder; the D4-B brief's loose "50/mo/tier" phrase
is superseded by the per-tier ladder it itself points at (DR-018 #2).

Anthropic-over-OpenAI: no LLM in billing; pure Stripe REST + HMAC.
"""

from __future__ import annotations

import os

from app.db.models import OrgTier


def _required(name: str) -> str:
    """Mirror of `r2.py:27-31`. Raise loudly at first use (never at import)
    so test collection and the Clerk-exempt health probe don't need Stripe
    env set, but a real billing call fails with an actionable message."""
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} env var not set")
    return val


class _Settings:
    """Lazy attribute accessors. `settings.STRIPE_WEBHOOK_SECRET` resolves
    env at access time (so monkeypatch.setenv in tests works without a
    reload), matching the spec's `settings.STRIPE_WEBHOOK_SECRET` call shape
    (`stripe_webhook_spec.md:203`)."""

    @property
    def STRIPE_SECRET_KEY(self) -> str:
        return _required("STRIPE_SECRET_KEY")

    @property
    def STRIPE_PUBLISHABLE_KEY(self) -> str:
        return _required("STRIPE_PUBLISHABLE_KEY")

    @property
    def STRIPE_WEBHOOK_SECRET(self) -> str:
        return _required("STRIPE_WEBHOOK_SECRET")

    @property
    def STRIPE_PRICE_SOLO(self) -> str:
        return _required("STRIPE_PRICE_SOLO")

    @property
    def STRIPE_PRICE_TEAM(self) -> str:
        return _required("STRIPE_PRICE_TEAM")

    @property
    def STRIPE_PRICE_ENTERPRISE(self) -> str:
        return _required("STRIPE_PRICE_ENTERPRISE")

    # Clerk auto-personal-org provisioning (POST /api/v1/webhooks/clerk).
    # Same lazy `_required` posture as the STRIPE_* props above: resolve env
    # at access time, raise loudly only when a real webhook call needs it, so
    # import-time / test collection / the Clerk-exempt health probe never
    # need these set. Tests `monkeypatch.setenv` the test-only values; prod
    # values are HF Space secrets (orchestrator-provisioned, DR-020 — build
    # to these EXACT names, do not invent).
    #   CLERK_WEBHOOK_SECRET  whsec_...  (Svix signing secret, dashboard)
    #   CLERK_SECRET_KEY      sk_...     (Clerk Backend API Bearer auth)
    @property
    def CLERK_WEBHOOK_SECRET(self) -> str:
        return _required("CLERK_WEBHOOK_SECRET")

    @property
    def CLERK_SECRET_KEY(self) -> str:
        return _required("CLERK_SECRET_KEY")


settings = _Settings()


# --- price <-> tier mapping (re-fetch source-of-truth uses THIS, §4.1) ---


def price_id_for_tier(tier: OrgTier) -> str:
    """Checkout direction: tier -> Stripe price id. Raises on `free`
    (you don't checkout into free) and on unknown."""
    mapping = {
        OrgTier.solo: settings.STRIPE_PRICE_SOLO,
        OrgTier.team: settings.STRIPE_PRICE_TEAM,
        OrgTier.enterprise: settings.STRIPE_PRICE_ENTERPRISE,
    }
    if tier not in mapping:
        raise ValueError(f"no Stripe price for tier {tier!r}")
    return mapping[tier]


def tier_for_price_id(price_id: str) -> OrgTier:
    """Webhook direction: Stripe price id (from the re-fetched Subscription,
    §4.1) -> our tier. Unknown price id -> `free` (fail safe: an unrecognized
    plan must NOT silently grant a paid tier). Reads env lazily so a test
    that only sets the three price envs it needs still works."""
    try:
        reverse = {
            os.environ.get("STRIPE_PRICE_SOLO"): OrgTier.solo,
            os.environ.get("STRIPE_PRICE_TEAM"): OrgTier.team,
            os.environ.get("STRIPE_PRICE_ENTERPRISE"): OrgTier.enterprise,
        }
    except Exception:  # pragma: no cover - defensive
        return OrgTier.free
    return reverse.get(price_id, OrgTier.free)


# --- soft-cap ladder (dispatches/D4_specs.md:37, DR-018 #2) ---
# None == unlimited (Enterprise). Bounds *monthly conversion jobs*, distinct
# from the HTTP-request rate limit in specs/rate-limit-abuse-v1.md:9.

MONTHLY_JOB_SOFT_CAP: dict[OrgTier, int | None] = {
    OrgTier.free: 5,
    OrgTier.solo: 50,
    OrgTier.team: 200,
    OrgTier.enterprise: None,
}

# Free tier: preview-only — one episode per dataset (Story #8,
# dispatches/D4_specs.md:149, `embodied-data convert --max-episodes 1`).
FREE_TIER_MAX_EPISODES = 1


# --- per-tier upload size cap (Track 4 edge-hardening) ---
# The DB-backed `jobs.py` presign flow has the client PUT bytes straight to
# R2, so the backend never streams the upload itself — the legacy
# `upload.py:35` 800 MB streaming guard does NOT cover this path. We instead
# gate on the client-declared `size_bytes` at presign time (jobs.py
# `presign_upload`): reject BEFORE issuing the R2 URL so an oversized file is
# never uploaded, never partially billed, never reaches the converter to OOM.
#
# The cap is a HARD ceiling per tier (not a default). `None` == no cap
# (Enterprise). Bytes, so the wire value is exact. Free's 800 MB matches the
# legacy `MAX_COMPRESSED_BYTES` (`upload.py:35`) so behavior is unchanged for
# the smallest plan; paid tiers get progressively more headroom.
_MB = 1024 * 1024
MAX_UPLOAD_BYTES: dict[OrgTier, int | None] = {
    OrgTier.free: 800 * _MB,
    OrgTier.solo: 2 * 1024 * _MB,  # 2 GB
    OrgTier.team: 8 * 1024 * _MB,  # 8 GB
    OrgTier.enterprise: None,  # uncapped
}
