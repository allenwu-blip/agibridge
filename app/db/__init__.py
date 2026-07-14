"""DB layer for agibridge commercial cut. New in W1 D1-D3.

Replaces the in-memory `app.api.session_store.SessionStore` (session_store.py:94-137)
which was the F-1-hobby single-tenant ephemeral implementation. Wiring into
the FastAPI handlers (`upload.py`, `status.py`, `download.py`) happens in
D4 alongside the Clerk JWT middleware that establishes `org_id` from JWT
claims; this module ships additively so the existing routes keep working
during the transition.

R3 (cross-org isolation) is THE invariant: every job lookup requires the
(job_id, org_id) tuple. See `job_store.py` and
`/Users/allenwu/.plans/agibridge-2026/_day1_research/tech_spec.md` §6 Risk 3.
"""
