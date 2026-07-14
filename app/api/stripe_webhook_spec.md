# Stripe Webhook Handler — Design Spec

**Cycle**: G · D3+ autonomous-drive
**Date**: 2026-05-14
**Status**: AUTHORITATIVE REFERENCE for backend-dev D4 implementation. Do NOT implement in this file.
**Scope**: subscription lifecycle webhooks only (`customer.subscription.{created,updated,deleted}`, `invoice.paid`, `invoice.payment_failed`). Other event types out of scope until DR-008 reopens.
**Verification posture**: every Stripe claim cites docs.stripe.com URL + access date 2026-05-14; every file claim cites `file_path:line_range`.

Source decisions: DR-003 (3-tier pricing), DR-008 (Stripe Checkout + Customer Portal + webhook with idempotency table). Schema already shipped: `StripeEvent` PK = event id at `app/db/models.py:258-273`; `Org.tier` at `models.py:117-127`; `Subscription` at `models.py:158-185`.

---

## 1. Webhook idempotency invariant

**Invariant.** For any given `event.id` (`evt_xxx`), the side-effect set is applied **at most once**. Duplicate deliveries return HTTP 200 with no DB mutation re-applied.

Required because Stripe delivers **at-least-once**. Per [docs.stripe.com/webhooks](https://docs.stripe.com/webhooks) (accessed 2026-05-14): "Webhook endpoints might occasionally receive the same event more than once. You can guard against duplicated event receipts by logging the event IDs you've processed."

**Storage primitive.** `stripe_events.id String(64) PRIMARY KEY` at `models.py:267`. A second INSERT raises `IntegrityError` (Postgres `23505` / SQLite `UNIQUE constraint failed`). We rely on the unique constraint, NOT on an application-level "did we see this?" SELECT (racy under concurrent deliveries).

**Race-safe transaction order.** All writes in one tx, with the event-row insert FIRST:

```
BEGIN TX
  INSERT INTO stripe_events (id, type, payload) VALUES (...)
    -- IntegrityError → ROLLBACK + return 200 duplicate
  re-fetch authoritative object via Stripe API   -- §4
  UPDATE orgs SET tier = ...; UPSERT subscriptions row
  UPDATE stripe_events SET processed_at = now() WHERE id = ...
COMMIT
```

If the tx crashes mid-way, Postgres rolls back the entire tx including the event row; Stripe retries find no row and reprocess cleanly. **The event-id INSERT must NOT live in a separate tx** from the state mutation — splitting them creates permanent state drift on crash (row says "seen", state never mutated, retry short-circuits).

---

## 2. Race-condition scenarios (test cases)

Stripe is explicit ([docs.stripe.com/webhooks](https://docs.stripe.com/webhooks), accessed 2026-05-14): "Stripe doesn't guarantee the delivery of events in the order that they're generated... Make sure that your event destination isn't dependent on receiving events in a specific order."

### 2.1 `subscription.updated` before `subscription.created`

Customer signs up Solo then upgrades to Team in seconds. Stripe emits created (solo) then updated (team); network delivers updated first.

**Wrong**: read tier from event payload → set `orgs.tier=team`, then `created` arrives → set `orgs.tier=solo`. Customer paid for Team, got Solo.

**Correct**: on every webhook, re-fetch `Subscription` from Stripe API (§4). API returns the latest state regardless of event order → both webhooks converge to `team`.

Test: `test_out_of_order_updated_before_created_uses_api_truth`.

### 2.2 `subscription.deleted` before final `invoice.paid`

Customer cancels mid-period. Source of truth for `orgs.tier` is **`subscription.status` from the API**, not the invoice. Per docs.stripe.com/api/subscriptions/object (accessed 2026-05-14), `subscription.status` is the canonical lifecycle indicator.

- On `subscription.deleted` → re-fetch → `status ∈ {canceled, incomplete_expired}` → set `orgs.tier=free`, but **persist `current_period_end`** (`models.py:174-176`) so tier-gating in handlers still grants access until period end.
- On `invoice.paid` for a canceled sub → no-op for `orgs.tier`; may emit a `usage_events` row for accounting.

Tier-gating in upload handler reads `current_period_end > now()`, decoupling webhook arrival order from access decisions.

Test: `test_deleted_before_final_invoice_paid_uses_subscription_status_truth`.

### 2.3 Retry storm — 5× same `event_id` in burst

Stripe retries with exponential backoff up to **3 days** (docs.stripe.com/webhooks, accessed 2026-05-14: "up to three days with an exponential back off"). Combined with pod restarts, 2-5 deliveries can land within ~30s.

First delivery: INSERT succeeds → process → COMMIT. Deliveries 2-5: `IntegrityError` on PK → handler returns `200 {"status":"duplicate","event_id":"evt_..."}` with no DB mutation, no Stripe API re-fetch. Under true parallel delivery (two replicas), Postgres serializes the INSERT — one wins, the other fails. SQLite tests cover the IntegrityError path; concurrent-Postgres test deferred to D5+ alongside Neon branch tokens (same posture as `tests/test_r3_cross_org_isolation.py:13-17`).

Test: `test_duplicate_event_id_returns_200_no_mutation`.

### 2.4 Network partition — commit succeeds, response packet drops

Backend commits tx and returns 200; TCP RST / NAT timeout drops the response; Stripe retries. Same path as §2.3 — PK violation → 200 duplicate → no double mutation. If the process is SIGKILL'd between commit and HTTP return, the row IS in the DB; the retry still hits PK → safe.

Test: `test_committed_then_crashed_retry_is_safe_idempotent`.

### 2.5 (Bonus) Dashboard "Resend"

Stripe's dashboard resend reuses the original `event.id`. Same path as §2.3.

Test: `test_dashboard_resend_old_event_is_duplicate`.

---

## 3. Signature verification + replay protection

### 3.1 Header format

Per [docs.stripe.com/webhooks/signatures](https://docs.stripe.com/webhooks/signatures) (accessed 2026-05-14):

```
Stripe-Signature: t=1492774577,v1=<hex_hmac_sha256>,v0=<test_only>
```

- `t` = Unix timestamp at signing
- `v1` = HMAC-SHA256 of `f"{t}.{raw_body_bytes}"` keyed by webhook secret
- `v0` is test-only — Stripe doc: "To prevent downgrade attacks, ignore all schemes that aren't `v1`."

### 3.2 Verification algorithm

1. Extract `t` and all `v1` signatures from header.
2. Compute `expected = HMAC-SHA256(secret, f"{t}.{raw_body}")`.
3. Constant-time compare (`hmac.compare_digest`) — defeats timing attacks.
4. Check `abs(now() - t) < tolerance`. Reject if outside.

**Critical**: signature is over **raw request bytes**, not parsed JSON. Use FastAPI `await request.body()` (bytes); never `await request.json()` then re-serialize.

### 3.3 Timestamp tolerance

Stripe library default: **5 minutes** (docs.stripe.com/webhooks/signatures, accessed 2026-05-14: "Our libraries have a default tolerance of 5 minutes between the timestamp and the current time").

**Our recommendation: 300 seconds (5 min), unchanged from default.** Tighter (60s) → false rejects on retry storms (Stripe preserves the original `t`). Looser (1 hr) → extends replay window. Hard rule from Stripe: "Don't use a tolerance value of 0."

### 3.4 Replay attack scenario

Attacker (compromised proxy / leaked log) captures a legitimate `customer.subscription.created` for `tier=enterprise` sent at 14:00. At 14:30 they replay verbatim hoping to escalate their own org's tier. The captured `v1` is cryptographically valid — signature alone is insufficient.

**Layered defenses:**
1. **Timestamp tolerance** rejects: `30 min > 5 min`. First line.
2. **`stripe_events.id` PK** rejects even within the 5-min window: PK collision on `evt_xxx`.
3. **HTTPS-only ingress** (HF Space / Vercel enforce). Prevents trivial in-flight capture.
4. **(W3+) IP allowlist** for Stripe's published IPs per https://docs.stripe.com/ips (accessed 2026-05-14) — Stripe recommends both signature + IP allowlist.

MVP ships layers 1+2+3.

### 3.5 Secret management

`STRIPE_WEBHOOK_SECRET` (`whsec_...`) in HF Space / Vercel env, **distinct** from Stripe API key (`sk_...`). Never committed.

---

## 4. Source-of-truth pattern (CRITICAL)

### 4.1 Re-fetch on every webhook

Per [docs.stripe.com/webhooks#best-practices](https://docs.stripe.com/webhooks#best-practices) (accessed 2026-05-14): "You can also retrieve the API resource from the Stripe API to access the latest and up-to-date object definition."

**Policy: re-fetch on every webhook driving `orgs.tier` or `subscriptions`.** Use event payload only to extract object id (`sub_xxx`, `in_xxx`); call `stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])` for authoritative state. Without this, §2.1 corrupts tier; with it, every webhook converges regardless of order.

Cost: ~50-150ms per webhook. At <50 customers × ~10 events/mo = ~500/mo, negligible. If Stripe API fails (rate-limit, 5xx) → handler returns 5xx → Stripe retries; `stripe_events` row not inserted (same tx) → clean retry semantics.

### 4.2 Reconciliation cron (W3, not MVP)

Per tech_spec §6 Risk 2 (`_day1_research/tech_spec.md:208-215`): nightly compare Stripe API vs DB `orgs.tier`, alert drift via Sentry — don't auto-correct (hides bugs). Module: `app/jobs/reconcile_subscriptions.py`, env-gated. Defer to W3.

### 4.3 Tier-gating reads `current_period_end`

When a paid sub cancels, the customer keeps access through `current_period_end` (`models.py:174-176`). Story #8 tier-gate reads this, not just `orgs.tier`.

---

## 5. Idempotency-key strategy beyond `event_id`

### 5.1 Inbound — `event_id` PK is sufficient for MVP

`stripe_events.id` (`models.py:267`) handles ~99% of dedup cases.

### 5.2 Outbound — `Idempotency-Key` header on OUR calls to Stripe

Per [docs.stripe.com/api/idempotent_requests](https://docs.stripe.com/api/idempotent_requests) (accessed 2026-05-14): "When creating or updating an object, use an idempotency key. Then, if a connection error occurs, you can safely repeat the request without risk of creating a second object." Stripe stores the response for "at least 24 hours."

For `create_stripe_customer_for_org` triggered by Clerk's `organization.created` webhook:

```
idempotency_key = f"customer-create:{clerk_org_id}"  # deterministic
customer = stripe.Customer.create(
    metadata={"clerk_org_id": clerk_org_id},
    idempotency_key=idempotency_key,
)
```

A **deterministic** key (Clerk `org_xxx`) is correct here — intent is "exactly one Stripe customer per Clerk org". Random UUID per retry would create N customers, defeating the point. Stripe recommends V4 UUID OR another high-entropy string; opaque deterministic prefixes satisfy entropy and avoid collisions.

Caveats from docs: "All POST requests accept idempotency keys. Don't send idempotency keys in GET and DELETE requests." "Avoid using sensitive data (for example, email addresses) as idempotency keys." → use Clerk org_id (opaque), not email.

### 5.3 Triple-key (event_id + customer_id + subscription_id) — when?

Stripe docs (accessed 2026-05-14): "In some cases, two separate Event objects are generated and sent. To identify these duplicates, use the ID of the object in `data.object` along with the `event.type`."

| Scenario | `event_id` PK enough? |
|---|---|
| §2.1 out-of-order | Yes — distinct event ids |
| §2.3 retry storm | Yes — PK collision |
| Two distinct legitimate `subscription.updated` events for same sub (e.g. plan change + quantity change in one Portal session) | Yes — both have distinct ids and BOTH need processing |
| Stripe internal: two `event.id`s for same logical event | NO — would process twice |

**MVP**: `event_id` alone. Because we always re-fetch (§4.1), the second processing of a duplicated-logical event converges to the same final state — wasteful but not corrupting. Add a triple-key UNIQUE INDEX (`(payload->>'object_id')`, `type`, `received_at::date`) only if we observe this case in prod logs, OR if we add a non-re-fetched code path. Tracked as W3+ hardening.

---

## 6. Handler control flow (pseudocode for D4)

D4 MUST follow this ordering. Deviation requires an amendment to this spec.

```
POST /api/v1/billing/webhook

1. raw_body = await request.body()              # bytes, NOT parsed JSON
2. sig = request.headers.get("stripe-signature")
   if not sig: return 400                       # Stripe never omits this
3. try:
       event = stripe.Webhook.construct_event(
           payload=raw_body, sig_header=sig,
           secret=settings.STRIPE_WEBHOOK_SECRET,
           tolerance=300,                       # 5 min, §3.3
       )
   except stripe.SignatureVerificationError:
       return 400                               # bad sig OR timestamp out of tolerance
4. BEGIN async DB transaction (single tx for all writes)
5.     try:
           session.add(StripeEvent(id=event["id"], type=event["type"],
                                   payload=event))
           await session.flush()                # surface IntegrityError now
       except IntegrityError:
           await session.rollback()
           return 200 {"status":"duplicate","event_id":event["id"]}
6.     if event["type"].startswith("customer.subscription."):
           sub_id = event["data"]["object"]["id"]
           sub = stripe.Subscription.retrieve(sub_id,
                                              expand=["items.data.price"])
           await apply_subscription_state(session, sub)
       elif event["type"] == "invoice.paid":
           inv = stripe.Invoice.retrieve(event["data"]["object"]["id"])
           await record_invoice_paid(session, inv)
       elif event["type"] == "invoice.payment_failed":
           inv = stripe.Invoice.retrieve(event["data"]["object"]["id"])
           await flag_payment_failure(session, inv)
       # other types: row inserted for audit, no mutation
7.     UPDATE stripe_events SET processed_at = now() WHERE id = event["id"]
8. COMMIT
9. return 200 {"status":"processed","event_id":event["id"]}

Any unhandled exception in 4-8 → ROLLBACK → 500 → Stripe retries.
Idempotency invariant holds: the stripe_events row rolled back with the tx.
```

**Why step 5 is INSIDE the tx:** if the INSERT is its own committed tx before the state-mutation tx, a crash between them leaves a row marked "seen" with no state mutation; the retry short-circuits → permanent drift. This is the canonical webhook bug; the fix is exactly the above ordering.

**`processed_at` vs `received_at`** (`models.py:270-273`): `received_at` defaults to `now()` on row insert; `processed_at` set after side-effects. `received_at < now() - 5 min AND processed_at IS NULL` → stuck-handler tripwire; D4 should add a Sentry alert on this.

**Routing:** D4 creates `app/api/billing.py` exposing `POST /api/v1/billing/{checkout,portal,webhook}`. **Webhook MUST be exempt from the Clerk JWT middleware** — it authenticates via Stripe signature only. Middleware exclusion list in `app/main.py` must add `/api/v1/billing/webhook`.

**Dependency:** D4 adds `stripe>=8.0,<9.0` to `pyproject.toml:9-22` (currently absent at `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/pyproject.toml`). Pin tight to match the `embodied-data==0.3.1` discipline.

---

## 7. Verification matrix (D4 PR review checklist)

| Invariant | Test | Spec § |
|---|---|---|
| Duplicate event_id → 200, no mutation | `test_duplicate_event_id_returns_200_no_mutation` | §1, §2.3 |
| Out-of-order updated/created → API truth | `test_out_of_order_updated_before_created_uses_api_truth` | §2.1, §4.1 |
| deleted-before-final-paid → subscription.status truth | `test_deleted_before_final_invoice_paid_uses_subscription_status_truth` | §2.2 |
| Commit-then-crash retry is safe | `test_committed_then_crashed_retry_is_safe_idempotent` | §1, §2.4 |
| Dashboard resend = duplicate | `test_dashboard_resend_old_event_is_duplicate` | §2.5 |
| Missing signature header → 400 | `test_missing_signature_header_returns_400` | §3.2 |
| Bad signature → 400, no DB write | `test_bad_signature_returns_400_no_db_write` | §3.2 |
| Replay outside tolerance → 400 | `test_timestamp_outside_tolerance_returns_400_replay` | §3.3, §3.4 |

---

## 8. Sources

**Stripe docs (all accessed 2026-05-14):**
- https://docs.stripe.com/webhooks — at-least-once delivery, no ordering guarantee, 3-day retry, duplicate handling
- https://docs.stripe.com/webhooks/best-practices — re-fetch on every webhook, quick 2xx, async processing
- https://docs.stripe.com/webhooks/signatures — `Stripe-Signature` format, HMAC-SHA256, 5-min default tolerance, ignore non-v1
- https://docs.stripe.com/api/idempotent_requests — `Idempotency-Key` outbound, 24h retention
- https://docs.stripe.com/api/subscriptions/object — `subscription.status` canonical lifecycle field
- https://docs.stripe.com/ips — Stripe webhook IP ranges (W3+ allowlist)

**Local sources:**
- `app/db/models.py:117-127` — `Org.tier`
- `app/db/models.py:158-185` — `Subscription`, `current_period_end:174-176`
- `app/db/models.py:258-273` — `StripeEvent`, PK at 267, `processed_at` 270, `received_at` 271-273
- `app/db/job_store.py:36-127` — DB-store pattern the handler mirrors
- `pyproject.toml:9-22` — current deps; `stripe` to be added in D4
- `_day1_research/tech_spec.md:208-215` — Risk 2 mitigation this spec executes
- `DECISIONS.md:58-63` — DR-008 locked
- `tests/test_r3_cross_org_isolation.py:13-17` — test infra posture (sqlite for ORM-portable logic, Postgres deferred to D5+)
