# DECISION REQUEST — backend `subprocess_runner.py` blocks DoD #2 integration test

**Filed by**: frontend-dev
**Date**: 2026-05-04 (W1 close-out execution turn)
**Branch**: `frontend/v0-spa` (off `origin/backend/v0-skeleton`)
**Severity**: medium — DoD #2 of frontend brief unreachable on default `uvicorn[standard]` install + default PATH

## Summary

While running the brief's DoD #2 ("Manual integration test against backend running locally: drag-drop AgiBot Beta task 675 fixture, see `pending → running → done`, download succeeds"), the backend's `app/api/subprocess_runner.py` background task fails to complete the conversion in two distinct ways depending on the asyncio loop policy:

1. **Default `uvicorn[standard]` install (uvloop)**: `app/api/subprocess_runner.py:95` passes `process_group=0` to `asyncio.create_subprocess_exec`. uvloop's `subprocess_exec` does NOT accept the `process_group` keyword. The background task throws `ValueError: unexpected kwargs: process_group`, the convert never starts, and the session sticks in `state=running` until the 30-min PurgeReaper. The frontend's poller correctly observes the clamp at 99 (spec §2.2.1 verified) but no `done` transition ever happens.
2. **`--loop asyncio` workaround**: subprocess starts but `app/api/subprocess_runner.py:185` falls back to `["python", "-m", "embodied_data.cli", ...]` when `embodied-data` is not on PATH. On a typical macOS dev shell `python` is absent (only `python3` and venv-relative `python` exist), so the subprocess silently fails with FileNotFoundError before emitting JSON. The session also sticks in `running`. Setting `PATH="$VENV/bin:$PATH"` before launching uvicorn unblocks this.

## Cross-agent contract delta check (brief instrumentation signal #2)

The committed contract in `app/schemas.py` + `app/api/*.py` route signatures **fully matches** spec v2.2 §2 — no contract divergence. The two issues above are pure implementation bugs in `subprocess_runner.py`, not contract drift. The frontend's API client mirrors backend shapes 1:1 (see citations in `frontend/src/lib/types.ts` and `frontend/src/lib/api.ts`).

## Files affected

- `app/api/subprocess_runner.py:89-97` — `process_group` kwarg passed unconditionally to `asyncio.create_subprocess_exec` (incompatible with uvloop).
- `app/api/subprocess_runner.py:183-186` — fallback to `["python", -m, ...]` literal does not handle environments where only `python3` or `sys.executable` is reliable.

## Suggested fixes (backend brief, not frontend)

For (1):

```python
# Detect uvloop and skip process_group when it's incompatible.
import asyncio
loop = asyncio.get_running_loop()
loop_module = type(loop).__module__
if platform.system() != "Windows" and "uvloop" not in loop_module:
    kwargs["process_group"] = 0
elif platform.system() != "Windows":
    # uvloop fallback: start_new_session is supported on POSIX and gives
    # the equivalent isolation for killpg().
    kwargs["start_new_session"] = True
```

For (2): use `sys.executable` instead of literal `"python"`:

```python
import sys
if not shutil.which("embodied-data"):
    base = [sys.executable, "-m", "embodied_data.cli"] + base[1:]
```

## Workaround used during this dispatch turn

To verify DoD #2 of the frontend brief I started backend with both:

```bash
PATH="/path/to/.venv/bin:$PATH" \
  /path/to/.venv/bin/uvicorn app.main:app --host 127.0.0.1 \
    --port 7860 --workers 1 --loop asyncio
```

`pending → running → done` transition + zip download both verified end-to-end. See `frontend/scripts/integration-probe.mjs` for the automated probe; it documents this prerequisite in its header.

## Frontend DoD impact

- DoD #2 — **PASS with workaround** (integration probe green; see `frontend/scripts/integration-probe.mjs` output).
- DoD #8 — **PASS**: backend's `app/api/status.py:72-73` clamp at 99 verified live during the uvloop-stuck run (16+ consecutive 99 readings while subprocess was actually erroring out — the clamp held).
- All other DoD items unaffected.

## Action requested

Allen, please decide:

- **A**: route this to backend-dev as a follow-up patch on `backend/v0-skeleton` (preferred — small surgical fix, keeps `uvicorn[standard]` default).
- **B**: change `pyproject.toml` to drop `uvicorn[standard]` and use plain `uvicorn` (no uvloop). Loses small perf benefit on `/status` polling endpoint but bypasses (1).
- **C**: leave as-is and document the `--loop asyncio` + `PATH=` invocation in the README.

I have NOT modified backend code on this branch — that's outside the frontend brief scope, and merge-conflict-prone given the resolution chain `backend/v0-skeleton → main → frontend/v0-spa → main`. The backend agent and Allen own the call.
