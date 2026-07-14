# agibridge Outbound Webhook System — Design Spec

**Cycle**: AB · Wave 3 sequential
**Date**: 2026-05-14
**Status**: DRAFT contract (D5+ implementation). MVP D4 does NOT ship outbound webhooks — published-tier feature, not free-tier.
**Scope**: us → customer endpoints. Inverts the inbound pattern from Cycle G `app/api/stripe_webhook_spec.md` (Stripe → us).
**Verification posture**: every vendor claim cites docs URL + access date 2026-05-14; every local claim cites `file_path:line_range`.

Source decisions: DR-008 inbound Stripe webhook pattern (we mirror Stripe's conventions on the way out); DR-007 R2 per-org isolation (R3 invariant on payload scoping); `sdk/python-spec.md` §8 (the SDK already ships a `Webhook.construct_event` helper modeled on the scheme defined here).

---

## 1. Event catalog

### 1.1 MVP-launch events (4)

These ship in the first outbound-webhook release (D5+, no commitment to bundle with MVP D4).

| `type` | Trigger | Receiver action (typical) |
|---|---|---|
| `job.completed` | Conversion job transitions to `done` (terminal success state per `app/db/models.py` Job state machine) | Pull converted dataset, push to downstream system (HF Hub, W&B Artifact, MLflow tracking) |
| `job.failed` | Conversion job transitions to `failed` (terminal failure) | Page on-call, retry pipeline, surface error envelope to user |
| `subscription.upgraded` | `Org.tier` mutated from lower to higher tier (e.g. `solo`→`team`) by Stripe webhook handler at `app/api/stripe_webhook_spec.md` §6 step 6 | Increase user's internal quota, unlock feature flags |
| `subscription.canceled` | `Subscription.status` transitions to `canceled` per Stripe `subscription.deleted` event (`stripe_webhook_spec.md` §2.2) | Down-tier user in customer's internal app, schedule data export |

### 1.2 Roadmap-deferred events (not in first outbound release)

Documented in this spec only so receivers can plan their event-router skeleton. None ship until the listed condition lands.

| `type` | Defer trigger |
|---|---|
| `org.member.invited` | First customer requesting team-onboarding automation |
| `org.member.removed` | Same as above |
| `usage.cap.reached` | First customer requesting auto-throttle on internal quota |
| `job.expired` | First customer requesting retention-window automation per DR-018 default #3 |
| `job.queued` / `job.running` | Defer indefinitely. Polling `GET /jobs/{id}` already covers this — webhook fan-out for non-terminal transitions is wasteful and creates ordering pain (§3 ordering) without paying for itself |

**Why a tight launch catalog**: each event we publish is an implicit forward-compat commitment. Receivers wire dispatch tables on `event.type`; adding events later is safe, renaming or removing is not. Ship 4, learn from prod, then add.

---

## 2. Event payload schema

### 2.1 Envelope (every event)

Modeled on the Stripe envelope (`stripe_webhook_spec.md` §6 step 5 shows what we already parse on the inbound side). Receivers using the agibridge Python SDK get this for free via `Webhook.construct_event` per `sdk/python-spec.md` §8.

```json
{
  "id": "evt_01HKZ8X3FQ7Y6R2WM4VN9P0T5A",
  "type": "job.completed",
  "created": 1747200000,
  "livemode": true,
  "data": {
    "object": { "...": "per-type, see §2.2" }
  }
}
```

Field types:

- `id` — string, `evt_` + 26-char Crockford base32 ULID. Stable across retries (receivers MUST dedup on this; see §4 idempotency-key).
- `type` — string, dot-delimited namespace (`{resource}.{verb}`). Matches Stripe convention (`docs.stripe.com/webhooks` accessed 2026-05-14).
- `created` — integer, unix seconds at event creation. NOT the delivery time. Receivers use this for sequencing within a single resource (§3).
- `livemode` — boolean. `true` for production tenant, `false` for sandbox / test-mode events. Mirrors Stripe `livemode` field (`docs.stripe.com/webhooks` accessed 2026-05-14).
- `data.object` — per-type payload. See §2.2.

**No top-level fields outside this envelope.** Custom metadata goes under `data.object` per-type — never as sibling keys of `data`. This is the same invariant Stripe enforces (`docs.stripe.com/webhooks` accessed 2026-05-14) and the reason its receivers' code is portable across event types.

### 2.2 Per-event `data.object` shapes

**`job.completed`**:

```json
{
  "id": "job_01HKZ8...",
  "org_id": "org_01HKZ7...",
  "status": "done",
  "input_filename": "agibot_world_episodes_0_to_99.tar.gz",
  "input_format": "agibot_world_v2",
  "output_format": "lerobot_v3",
  "output_url": "https://api.agibridge.com/api/v1/jobs/job_01HKZ8.../presign-download",
  "output_size_bytes": 2_431_002_112,
  "episode_count": 100,
  "started_at": 1747199100,
  "completed_at": 1747200000,
  "duration_seconds": 900
}
```

**`job.failed`**: same shape as `job.completed`, minus `output_*` fields, plus:

```json
{
  "status": "failed",
  "error": {
    "code": "schema_validation_failed",
    "message": "Action vector dtype mismatch at episode 47: expected float32, got float64",
    "suggestion": "Re-record episode 47 with the policy at https://github.com/huggingface/lerobot/issues/2689"
  }
}
```

Error envelope shape `{code, message, suggestion}` mirrors the `embodied-data` lib emit pattern at `_emit.py:32-46` (per DR-009 reasoning in `DECISIONS.md:69`) and the SDK's `AgibridgeError` surface at `sdk/python-spec.md:159-191`.

**`subscription.upgraded`** / **`subscription.canceled`**:

```json
{
  "id": "sub_01HKZ8...",
  "org_id": "org_01HKZ7...",
  "tier_before": "solo",
  "tier_after": "team",
  "current_period_end": 1749792000,
  "changed_at": 1747200000
}
```

`current_period_end` mirrors the Stripe API field at `app/db/models.py:174-176` so receivers can grant access until period end on cancel (same semantics as inbound — see `stripe_webhook_spec.md` §4.3).

### 2.3 R3 invariant on payload scoping (cross-org isolation)

**Invariant.** Every event body MUST contain `data.object.org_id` AND that `org_id` MUST match the receiving endpoint's owning org. The dispatch layer enforces this; under no circumstance does an event for `org_A` get delivered to an endpoint registered by `org_B`.

Concretely: when an endpoint is created (§6), the row is bound to a Clerk `org_id` (foreign key to `orgs.id`). The dispatch query is `SELECT endpoint FROM webhook_endpoints WHERE org_id = $1 AND $2 = ANY(subscribed_types)` — there is no path that joins `endpoints` to `jobs` without going through `org_id`. This mirrors the per-org R2 prefix isolation pattern from `DECISIONS.md:51-56` (DR-007) and the cross-org isolation invariant from `dispatches/D4_specs.md` §3 amendment #6.

Payloads never reference another org's resources. A `job.completed` body for `org_A` never includes a foreign-key string like `org_id: org_B` or a sibling job from a different org — the SELECT scope above is the only join.

---

## 3. Delivery semantics

### 3.1 At-least-once

Same posture as Stripe (`docs.stripe.com/webhooks` accessed 2026-05-14): "Webhook endpoints might occasionally receive the same event more than once. You can guard against duplicated event receipts by logging the event IDs you've processed." We adopt the same wording in the customer-facing docs because the inversion is symmetric — when we're the sender, we make the same guarantee we expect from Stripe.

### 3.2 No ordering guarantee

Quoting Stripe verbatim (`docs.stripe.com/webhooks` accessed 2026-05-14): "Stripe doesn't guarantee the delivery of events in the order that they're generated... Make sure that your event destination isn't dependent on receiving events in a specific order."

We adopt the same posture. Two concrete scenarios receivers must handle:

- `job.completed` arrives before `subscription.upgraded` even though the upgrade was committed first.
- Two `subscription.*` events for the same `sub_id` arrive out of order during a portal session (matches the inbound out-of-order pattern from `stripe_webhook_spec.md` §2.1).

**Source-of-truth pattern (mirrors inbound §4.1)**: receivers requiring authoritative state on a webhook should re-fetch via the agibridge REST API (`GET /api/v1/jobs/{job_id}`, etc.) rather than treating the webhook body as canonical. The SDK `client.jobs.get(job_id)` per `sdk/python-spec.md` §3.3 returns the latest state regardless of event order. Webhook body is fine for "fire something cheap"; re-fetch for "mutate persistent state".

### 3.3 Retry schedule (exponential backoff, 3-day window)

Matches the Stripe live-mode pattern verbatim (`docs.stripe.com/webhooks` accessed 2026-05-14): "Stripe attempts to deliver events to your destination for up to three days with an exponential back off in live mode." We adopt 3 days because:

1. Robotics CI systems are laggy — overnight pipelines, batched re-runs, and HF Spaces cold-starts routinely take >1 hour to settle. 24h is too tight.
2. Standard Webhooks recommends "multi-day exponential backoff" (`github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md` accessed 2026-05-14): "starting with immediate retry, then 5 seconds, 5 minutes, 30 minutes, escalating to 24+ hours, with recommendation to add some level of random jitter to retries."

**Concrete schedule** (jittered ±20%):

| Attempt | Delay after prior attempt |
|---|---|
| 1 | immediate |
| 2 | 5 s |
| 3 | 5 min |
| 4 | 30 min |
| 5 | 2 h |
| 6 | 6 h |
| 7 | 18 h |
| 8 | 24 h |
| 9 | 24 h |

Total ≈ 75 hours ≈ 3.1 days. Permanent failure after attempt 9.

**Permanent-failure behavior**: event is marked `delivery_status = failed_permanent` in `webhook_deliveries` and surfaced in the customer's delivery dashboard (§7). We do NOT auto-disable the endpoint at MVP (Stripe doesn't explicitly document an auto-disable threshold — `docs.stripe.com/webhooks` accessed 2026-05-14 does not name one; we defer this to W3+ once we have prod failure data).

### 3.4 Failure-mode routing

Receiver response → retry decision:

| Response | Action |
|---|---|
| `2xx` | Mark `delivered`, stop retries |
| `4xx` (excluding `408`, `429`) | Mark `failed_permanent`, stop retries. Customer config error — retrying won't help |
| `408 Request Timeout`, `429 Too Many Requests` | Retry per §3.3 schedule |
| `5xx` | Retry per §3.3 schedule |
| Connection error (DNS, TCP reset, TLS handshake, our-side timeout >10s) | Retry per §3.3 schedule |

Matches the SDK retry policy for inbound calls at `sdk/python-spec.md` §6 — symmetric posture so customers writing both directions (consuming our SDK + receiving our webhooks) see consistent semantics.

---

## 4. Signing scheme

### 4.1 Header

```
Agibridge-Signature: t=1747200000,v1=<hex_hmac_sha256>
```

- `t` = unix timestamp at signing (seconds since epoch)
- `v1` = HMAC-SHA256 of `f"{t}.{raw_body_bytes}"` keyed by the endpoint's signing secret

Format matches Stripe's `Stripe-Signature` (`docs.stripe.com/webhooks/signatures` accessed 2026-05-14) verbatim. Rationale for cloning Stripe rather than Standard Webhooks `webhook-signature` (`github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md` accessed 2026-05-14):

1. Our target audience already implements Stripe webhooks (every paying SaaS customer does). They wire the agibridge verifier as a 5-line variation of code they already wrote — not a third signing scheme to learn.
2. The SDK `Webhook.construct_event` helper at `sdk/python-spec.md` §8 already commits to the Stripe-style format. Aligning here keeps the SDK code path identical for both directions.
3. Standard Webhooks adds `msg_id` to the signed string (signs `msg_id.timestamp.payload`). We get the same replay-protection benefit from `t` + the body containing `id` (the same value), at the cost of one extra signed field. Not worth diverging from the more widely-implemented Stripe scheme.

### 4.2 Verification (what receivers run)

The SDK ships this; receivers writing it from scratch follow Stripe's documented algorithm (`docs.stripe.com/webhooks/signatures` accessed 2026-05-14):

1. Extract `t` and `v1` values from the `Agibridge-Signature` header.
2. Compute `expected = HMAC-SHA256(secret, f"{t}.{raw_body}")`.
3. Constant-time compare `v1` against `expected` (`hmac.compare_digest` in Python; equivalent in other languages).
4. Check `abs(now() - t) < tolerance_seconds`. Reject if outside.

**Critical**: the signature is over **raw request bytes**, not parsed JSON. Per `stripe_webhook_spec.md` §3.2 (same invariant on our inbound side), receivers MUST use the raw HTTP body before any JSON parsing — re-serializing a parsed dict produces different bytes (whitespace, key order) and breaks the HMAC.

### 4.3 Multiple signatures during rotation

The header value supports comma-separated `v1` values, same as Stripe: `t=...,v1=<old_secret_hmac>,v1=<new_secret_hmac>`. Used during the secret-rotation grace window (§6.3). Receivers verify if ANY `v1` matches.

This avoids the "rotate secret = all-or-nothing cutover" failure mode. Standard Webhooks documents the same pattern (`github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md` accessed 2026-05-14: "The webhook-signature header is a space-delimited list supporting multiple signatures for zero-downtime key rotation"). Stripe uses `v1=...,v1=...`; we adopt Stripe's separator for the consistency reasons in §4.1.

---

## 5. Replay protection

### 5.1 Timestamp tolerance

**Default**: 300 seconds (5 minutes). Matches Stripe library default (`docs.stripe.com/webhooks/signatures` accessed 2026-05-14: "Our libraries have a default tolerance of 5 minutes between the timestamp and the current time").

**Configurable per endpoint**: dashboard (§6) lets customers raise tolerance up to 3600 seconds (1 hour) for laggy environments (GitHub Actions cold starts, customer-internal queue-buffered receivers). Cannot set to 0 — Stripe explicitly warns against this (`docs.stripe.com/webhooks/signatures` accessed 2026-05-14: "Don't use a tolerance value of 0").

**Why not lower the default**: under retry storms (§3.3) Stripe preserves the original `t`. A receiver that sets tolerance to 60s rejects legitimate retries that arrive >60s after the original signing time. 300s is the inhabited-by-default minimum for live systems.

### 5.2 Replay attack scenario

Attacker captures a `subscription.upgraded` for `tier_after=enterprise` and tries to replay it to escalate their tier. Layered defenses (mirrors `stripe_webhook_spec.md` §3.4 inbound):

1. **Timestamp tolerance** rejects: replay arrives outside 5-min window.
2. **Receiver-side `event.id` dedup** rejects within the window: receiver SHOULD store seen `evt_xxx` ids in a `processed_events` table with PK constraint, same pattern as our inbound `stripe_events.id` PK at `app/db/models.py:267`. The SDK docs explicitly call this out at the top of the §8 example.
3. **HTTPS-only delivery** (the dispatcher refuses to register `http://` endpoints; only `https://` accepted at `/api/v1/webhook-endpoints` creation).
4. **(W3+) Sender IP allowlist publication** — we publish our outbound webhook source IP ranges so receivers can firewall-allowlist. Matches Stripe's `docs.stripe.com/ips` pattern (accessed 2026-05-14). Defer to W3+ because at MVP we deliver from HF Space / Modal which don't pin egress IPs.

MVP ships layers 1-3.

---

## 6. Endpoint registration

### 6.1 Lifecycle

Customers register endpoints via dashboard or API. The MVP shape:

| Action | API endpoint | Dashboard |
|---|---|---|
| Create endpoint | `POST /api/v1/webhook-endpoints` | "Add endpoint" button on `/settings/webhooks` |
| List endpoints | `GET /api/v1/webhook-endpoints` | `/settings/webhooks` table |
| Update endpoint (URL, subscribed types, tolerance) | `PATCH /api/v1/webhook-endpoints/{id}` | edit row |
| Delete endpoint | `DELETE /api/v1/webhook-endpoints/{id}` | row action |
| Reveal secret (once, at creation) | included in `POST` response | shown once with copy button |
| Rotate secret | `POST /api/v1/webhook-endpoints/{id}/rotate` | rotate button |

Each endpoint row:

```
webhook_endpoints
  id              ULID PK
  org_id          FK → orgs.id  (R3 isolation)
  url             https-only string
  secret_hash     argon2id hash of the secret (the secret itself never persists)
  subscribed_types text[]  -- e.g. {"job.completed", "job.failed"}
  tolerance_seconds int default 300
  created_at      timestamptz
  disabled_at     timestamptz nullable
```

### 6.2 Multi-endpoint per org (max 5 at MVP)

Customers can register up to 5 endpoints per org. Use cases that drove the cap:

- Separate prod + staging endpoints.
- Separate per-team receivers (the ML-platform team owns `job.*` routing, the finance team owns `subscription.*` routing).
- Fan-out to multiple downstream systems (HF + W&B + MLflow — exactly the three integration patterns in `integrations/hf-datasets-push.md`, `integrations/wandb-artifacts.md`, `integrations/mlflow-tracking.md`).

5 is a budget-conscious launch cap, not a fundamental limit. Raise on customer request — DR-018-style accept-default.

### 6.3 Secret rotation

Rotation creates a new secret, returns it once, and keeps the old secret valid for **24 hours**. During the grace window the dispatcher signs with BOTH secrets and emits the header per §4.3. Customers update their verifier to accept the new secret, then the old secret expires automatically.

Mirrors the SDK API-key rotation pattern at `sdk/python-spec.md:51` (also 24h grace).

---

## 7. Observability for receivers

Customers need to debug their own integrations. Dashboard surface at `/settings/webhooks/{endpoint_id}/deliveries`:

- **Recent deliveries** (last 100): event id, event type, attempted_at, status, response_code, latency_ms.
- **Filter by status**: `delivered` / `pending_retry` / `failed_permanent`.
- **Replay**: customer-initiated re-send of a specific event id. Re-uses the same `evt_xxx` (so customer dedup still works) but bumps the `t` (re-signs with current timestamp).
- **Per-endpoint success rate** rolled up over 24h / 7d.

This is the minimum surface that lets a customer answer "did agibridge actually send the event I think it did?" without filing a support ticket. Stripe ships an equivalent surface (`docs.stripe.com/webhooks/quickstart` accessed 2026-05-14 describes the dashboard's delivery log); ours mirrors it.

---

## 8. Testing tools

### 8.1 Dashboard test sender

Every endpoint detail page has a "Send test event" dropdown. Customer picks a `type`, we synthesize a payload (with `livemode: false`), sign with the endpoint's secret, deliver. The synthesized payload includes a stable `evt_test_*` id (distinct prefix from prod `evt_*`) so the receiver's prod dedup tables don't collide with test traffic.

### 8.2 CLI tool (`agibridge listen`, W3+)

Models on Stripe CLI (`docs.stripe.com/webhooks/quickstart` accessed 2026-05-14: "stripe listen --forward-to localhost:4242/webhook"). The agibridge CLI ships as part of the Python SDK package (per `sdk/python-spec.md`) and exposes:

- `agibridge listen --forward-to localhost:8000/agibridge-webhook` — opens a long-lived connection to our backend; we tunnel live prod events from the dev-mode tenant to the local URL. Same UX as Stripe CLI listen.
- `agibridge trigger job.completed` — synthesizes a test event of the given type. Same UX as `stripe trigger`.

This is W3+ because it requires (a) backend tunnel-server work and (b) a non-trivial CLI bootstrap; not blocker for the first paying customer using webhooks (they can use `webhook.site` or `ngrok` to expose a local URL — both are mentioned in Stripe's quickstart and remain valid here).

### 8.3 Third-party tools we explicitly recommend

For customers who don't want to install the agibridge CLI for one-off testing:

- `webhook.site` — free, gives a stable HTTPS URL that captures POST bodies + headers. Adequate for "did the event fire and what was in it" inspection.
- `ngrok` — tunnels a local dev server to a public HTTPS URL. Adequate for end-to-end testing of receiver code against real prod events.

Stripe's quickstart names both (`docs.stripe.com/webhooks/quickstart` accessed 2026-05-14). We carry the same recommendation forward; no new tooling required at MVP.

---

## 9. Open [DECISION NEEDED] (D5+ implementation choices, not Allen-blockers)

These are spec-author defaults that downstream implementers can adopt without further ratification per the cycle's autonomy posture. Flagging for visibility:

1. **Bundle webhooks with first MVP or defer to first follow-on release?** Spec defaults to D5+ (post-MVP). Argument for bundling: HF / W&B / MLflow integrations are the actual wedge for embodied-AI buyers and webhooks unlock them. Argument for deferring: 60-day target is already tight per DR-002, webhook delivery infrastructure is ~1 week of engineering. Recommend defer.
2. **`evt_*` id format — ULID vs Stripe-style 24-char**? Spec defaults to ULID (26-char Crockford base32, sortable, opaque, no PII). Stripe uses 24-char custom. ULID is the open standard.
3. **Max endpoints per org at launch — 5 vs 10**? Spec defaults to 5; raise on request. Stripe defaults to 16 (`docs.stripe.com/webhooks` accessed 2026-05-14 documents a soft limit). 5 is conservative; the cost of raising later is zero.
4. **Permanent-failure auto-disable threshold**? Spec defaults to "no auto-disable at MVP, dashboard-only failure surfacing." Stripe's behavior is undocumented in their public docs (no explicit threshold named). Defer until prod failure data informs the choice.
5. **Sandbox/livemode wiring**? Spec defaults to `livemode` boolean field on every event matching Stripe. Implementation requires the backend to have a "test tenant" notion — could plumb the existing F-1-era "dev mode" remnants (`app/main.py:49-54` per DR-011) or introduce a fresh `Org.is_test` column. Recommend fresh column, additive migration.

---

## 10. Sources

**Vendor docs (all accessed 2026-05-14)**:

- https://docs.stripe.com/webhooks — at-least-once delivery, 3-day exponential backoff retry, no ordering guarantee, duplicate-event guidance, `livemode` field, Stripe-Signature header
- https://docs.stripe.com/webhooks/signatures — HMAC-SHA256 over `f"{t}.{body}"`, 5-min default tolerance, multiple v1 values for rotation, raw-bytes signing requirement
- https://docs.stripe.com/webhooks/quickstart — `stripe listen --forward-to`, `stripe trigger`, dashboard delivery log, webhook.site + ngrok recommendation
- https://docs.stripe.com/ips — Stripe webhook source IP publication pattern (deferred to W3+)
- https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md — alternative `webhook-id` / `webhook-timestamp` / `webhook-signature` scheme, multi-day retry backoff, key rotation via multiple signatures

**Local sources**:

- `app/api/stripe_webhook_spec.md` — inbound pattern this spec inverts (esp. §1 idempotency, §3 signature, §4 source-of-truth)
- `app/db/models.py:117-127` — `Org.tier` referenced in `subscription.*` events
- `app/db/models.py:158-185` — `Subscription`, `current_period_end:174-176`
- `app/db/models.py:258-273` — `StripeEvent` pattern receivers should mirror for outbound dedup
- `sdk/python-spec.md:246-275` — `Webhook.construct_event` SDK helper this spec defines the wire format for
- `sdk/python-spec.md:159-191` — `AgibridgeError` envelope the `job.failed` `data.object.error` field matches
- `DECISIONS.md` DR-007 (R2 per-org isolation, the basis for R3 invariant §2.3), DR-008 (Stripe inbound), DR-018 default #3 (retention windows)
- `dispatches/D4_specs.md` §3 amendment #6 — backend cross-org isolation invariant we extend to webhook payloads

---

**Word count** (excluding meta-header, sources, code samples): ~2,150 words — within 1,500–2,500 target.
