"""`POST /api/v1/demo/convert` — zero-signup "try it" conversion.

The single biggest friction in a zero-trust niche tool is "sign up before
you can see if it even works". This endpoint removes it: an anonymous
visitor on the public Landing page clicks one button and watches a REAL
AgiBot World -> LeRobot v3 conversion + the real 5-check validator output,
with no account.

SCOPE DECISION — bundled sample, NOT arbitrary anonymous upload.
  An unauthenticated endpoint that runs a subprocess on a file the caller
  supplies is an unacceptable DoS / abuse surface (zip bombs, adversarial
  archives, unbounded fan-out of converter processes — none of it gated by
  Clerk/org/tier). So the demo does NOT take an upload. It converts a
  FIXED, in-image minimized single-episode sample committed to the repo
  (`app/demo_assets/agibot_sample/` — a truncated REAL AgiBot Beta-675
  task; see `app/demo_assets/README.md`). It is under `app/` so it
  survives into the deployed HF Space + Docker image, unlike the
  `tests/fixtures` fixture the pytest suite uses (stripped on deploy).
  The visitor picks nothing and uploads nothing; the input is a server
  constant. This
  still delivers the value ("see a genuine AgiBot->LeRobot v3 conversion +
  validated output before signup") — just on our sample, not theirs.

REUSE, NOT FORK, of the verified conversion core (DR-019).
  The conversion runs through the SAME `SubprocessRunner.run(sess)` the
  authed `/api/v1/jobs` flow uses (`app/api/jobs.py:482`,
  `app/api/subprocess_runner.py`). No `--max-episodes`, no weakened
  validation: `sess.max_episodes` stays None so the argv is byte-identical
  to the DR-019-verified command, and `--verify` runs the full 5-check
  validator. We only swap the auth + storage layer AWAY: input is a local
  copy of the bundled fixture (no R2 pull), output is read off local disk
  and summarized inline (no R2 push), and nothing is persisted.

Auth posture — Clerk-EXEMPT (same structural reason as `/api/v1/events`,
`app/api/clerk_auth.py:61-69`): the whole point is that it fires BEFORE a
user has an org-bearing JWT. It is therefore treated as fully untrusted:
it takes NO body/upload (the input is fixed), it is rate-limited per IP by
the existing slowapi limiter, it is single-flight via a process-global
lock, and it touches NONE of the R3 surface — no org is read or derived,
no `jobs` / `usage_events` row is written, no per-org R2 prefix is
touched. The only thing it writes is one anonymous, non-PII
`funnel_events` row (`kind="demo_run"`), the exact same sibling-table
posture `/api/v1/events` uses so demo->signup conversion is measurable
without eroding R3 (`app/db/models.py:290-337`).

Never break Landing: a demo that fails must DEGRADE, not 500. Every
internal failure (converter crash, timeout, missing fixture) is mapped to
a clean `200 {ok:false, ...}` body the frontend renders as a soft "the
demo hit a snag" with a sign-up CTA. The only non-200s are the deliberate
back-pressure codes: `429` (rate-limited, slowapi house envelope) and
`503` (another demo already running — single-flight).

Anthropic-over-OpenAI: no LLM here; subprocess + filesystem only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.api.session_store import Session, _root_dir
from app.api.subprocess_runner import SubprocessRunner, map_outcome_to_state
from app.db.models import FunnelEvent
from app.db.session import get_session
from app.schemas import State

logger = logging.getLogger("agibridge.demo")

router = APIRouter()

# The bundled sample. An in-image, minimized REAL AgiBot Beta-675 task
# (1 episode, first 20 frames truncated from the full fixture — see
# `app/demo_assets/README.md`). It lives under `app/` ON PURPOSE: the
# deployed HF Space strips `tests/fixtures` (`.github/workflows/hf-sync.yml`
# `rm -rf tests/fixtures`) and the Docker image excludes `tests/` entirely
# (`.dockerignore`), so a `tests/`-rooted sample is absent on the live
# Space. `app/demo_assets/` survives both. It is a server constant — the
# caller never chooses or uploads it.
_DEMO_FIXTURE = Path(__file__).resolve().parent.parent / "demo_assets" / "agibot_sample"
_DEMO_FROM = "agibot"
_DEMO_TO = "lerobot-v3"

# Per-IP cap. MUCH stricter than the cheap-probe limits in
# `app/api/rate_limit.py` (`/health` 60/min, `/billing/webhook` 120/min,
# `/events` 120/min) because — unlike those — every accepted call here
# SPAWNS A SUBPROCESS that does real conversion work. 5/min/IP lets a
# curious visitor retry a couple of times without 429 noise while making a
# per-IP flood economically pointless; the process-global single-flight
# lock below is the real concurrency bound, this just caps the queue. This
# matches the spirit of `specs/rate-limit-abuse-v1.md`'s stricter
# "Unauthenticated (demo)" row referenced at `app/api/rate_limit.py:48-51`.
DEMO_LIMIT = "5/minute"

# Demo wall-clock. The bundled sample converts + validates in ~1.5 s on
# real infra (measured: 1 episode / 1090 frames). `SubprocessRunner` has
# its own 25-min internal cap (`subprocess_runner.py:38`) but that is sized
# for full authed datasets; for a fixed 1.5 MB sample anything past ~90 s
# means the box is wedged, so we wrap the run in a tighter ceiling so a
# hung demo can never hold the single-flight lock for 25 minutes and
# starve Landing. On timeout we degrade to a clean 200 {ok:false}.
DEMO_WALL_CLOCK_S = 90

# Single-flight: this Space is one small box. Two concurrent demo
# subprocesses (or a demo racing an authed job) is exactly the unbounded
# fan-out we refuse. A process-global lock, tried NON-blocking, mirrors the
# authed flow's single-flight intent (`app/api/jobs.py:296-297`,
# `subprocess_runner.py:64-67`) — if it's held we 503 "busy, try again"
# instead of queuing/spawning.
_demo_lock = asyncio.Lock()

# The verified runner is process-global (single uvicorn worker, spec §3),
# wired by `app/main.py` exactly like the jobs router.
_runner: SubprocessRunner | None = None


def set_state(runner: SubprocessRunner) -> None:
    global _runner
    _runner = runner


def _result(status: int, body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content=body)


def _busy_503() -> JSONResponse:
    """Another demo conversion is already in flight (single-flight). 503 +
    Retry-After so the frontend can show a calm "busy, try again" instead
    of a spinner that never resolves. Distinct from the 429 rate-limit
    (that's per-IP volume; this is global concurrency)."""
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "state": "busy",
            "message": "The demo box is converting another sample right now.",
            "suggestion": "Give it a few seconds and try again.",
        },
        headers={"Retry-After": "15"},
    )


def _degraded(message: str) -> dict[str, Any]:
    """A demo that could not produce a result must still be a clean 200 so
    Landing renders a soft 'snag' card with a sign-up CTA — never a raw 500
    / blank page (this is also the Track-4-spirit no-broken-Landing rule)."""
    return {
        "ok": False,
        "state": "error",
        "message": message,
        "suggestion": "This is just our demo box being busy — your own data isn't affected.",
    }


def _summarize_output(out_dir: Path) -> dict[str, Any]:
    """Read the REAL converted LeRobot v3 dataset off local disk into a
    tiny honest summary. Nothing is faked: `meta/info.json` is the
    converter's own manifest; the file tree + byte size are the real
    output. We deliberately read this off disk rather than the convert
    stdout JSON so `subprocess_runner.py` (the DR-019 byte-unchanged core)
    needs no change to expose extra fields."""
    info: dict[str, Any] = {}
    info_path = out_dir / "meta" / "info.json"
    try:
        info = json.loads(info_path.read_text())
    except (OSError, ValueError):
        info = {}

    files: list[str] = []
    total_bytes = 0
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(out_dir).as_posix()
            files.append(rel)
            try:
                total_bytes += p.stat().st_size
            except OSError:
                pass

    return {
        "codebase_version": info.get("codebase_version"),
        "episodes": info.get("total_episodes"),
        "frames": info.get("total_frames"),
        "tasks": info.get("total_tasks"),
        "fps": info.get("fps"),
        "output_bytes": total_bytes,
        # Cap the tree so a future multi-file format can't bloat the
        # response; the bundled sample emits 6 files.
        "file_tree": files[:32],
    }


async def _record_demo_run(db: AsyncSession, anon_id: str | None, summary_meta: dict) -> None:
    """One anonymous, non-PII `funnel_events` row so demo->signup is
    measurable. SAME sibling-table posture as `/api/v1/events`
    (`app/api/events.py:186-192`): no org is read/derived, `funnel_events`
    has no FK, so R3 is untouched. `anon_id` is the client's localStorage
    UUID if it sent one (optional — the demo works without it); a missing
    id gets a server-side random correlator so the row is still countable
    without ever storing PII. Best-effort: a metering write must never
    fail the user-visible demo."""
    try:
        db.add(
            FunnelEvent(
                anon_id=(anon_id or f"srv-{uuid.uuid4()}")[:64],
                kind="demo_run",
                meta=summary_meta,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()


@router.post("/demo/convert", include_in_schema=True, response_model=None)
@limiter.limit(DEMO_LIMIT)
async def demo_convert(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Run the bundled AgiBot sample through the UNCHANGED verified
    converter and return the real validator + dataset summary. Takes no
    input: the sample is a server constant. Never 500 — degrades to a
    clean 200 {ok:false} on any internal failure; only 429 (rate-limited)
    / 503 (busy) are non-200."""
    assert _runner is not None

    # The input is FIXED. Reject any attempt to smuggle a body/upload —
    # this endpoint must never become an arbitrary-anon-upload surface.
    # A non-empty body is a 200 {ok:false} (not a 4xx that would surface
    # as a scary error on Landing); the contract is "the demo takes
    # nothing".
    body = await request.body()
    if body.strip():
        return _result(
            200,
            {
                "ok": False,
                "state": "error",
                "message": "The demo runs a fixed sample and takes no input.",
                "suggestion": "Just press the button — there's nothing to upload.",
            },
        )

    # Optional anon_id from a query param ONLY (no body is accepted). It is
    # purely a funnel correlator; the demo works without it.
    anon_id = request.query_params.get("anon_id")
    if anon_id is not None:
        anon_id = anon_id.strip() or None

    if not _DEMO_FIXTURE.is_dir():
        # Should be impossible (the fixture is committed) — but degrade,
        # never 500.
        logger.error("demo fixture missing at %s", _DEMO_FIXTURE)
        return _result(200, _degraded("The demo sample is temporarily unavailable."))

    # Single-flight: try to take the lock WITHOUT waiting. Held => another
    # demo is mid-conversion on this one small box => clean 503, do not
    # spawn a second subprocess.
    if _demo_lock.locked():
        return _busy_503()

    async with _demo_lock:
        # Re-check inside the lock (TOCTOU): another request may have taken
        # it between the check and acquire.
        root = _root_dir() / f"demo-{uuid.uuid4()}"
        sess = Session(session_id=root.name, root=root)
        sess.from_format = _DEMO_FROM
        sess.to_format = _DEMO_TO
        # IMPORTANT: leave sess.max_episodes = None. The bundled sample is
        # already a single episode, and None keeps the embodied-data argv
        # byte-identical to the DR-019-verified command (no weakening of
        # the conversion core; --verify still runs all 5 checks).
        extract_dir = sess.in_dir / "dataset"

        try:
            extract_dir.mkdir(parents=True, exist_ok=True)
            sess.out_dir.mkdir(parents=True, exist_ok=True)
            # Copy the committed fixture into the scratch dataset dir the
            # verified runner expects (`subprocess_runner.py:_build_cmd`
            # reads `sess.in_dir/dataset`). copytree, not a zip round-trip:
            # we own the input, so there's no archive to extract.
            await asyncio.to_thread(shutil.copytree, _DEMO_FIXTURE, extract_dir, dirs_exist_ok=True)

            # ---- VERIFIED-WORKING CORE: unchanged subprocess call ----
            # Identical invocation to the authed path
            # (`app/api/jobs.py:482`). Wrapped in a tighter wall-clock so a
            # wedged box can't hold the single-flight lock for 25 min.
            try:
                outcome = await asyncio.wait_for(_runner.run(sess), timeout=DEMO_WALL_CLOCK_S)
            except TimeoutError:
                await _record_demo_run(db, anon_id, {"ok": False, "reason": "timeout"})
                return _result(
                    200,
                    _degraded("The demo took longer than expected and was stopped."),
                )
            # ----------------------------------------------------------

            state, code, message, _suggestion = map_outcome_to_state(outcome)

            if state != State.done:
                # Real converter failure (crash / validation FAIL / etc.).
                # Honest but soft: surface the wrapper's own message, no
                # stderr leak, clean 200.
                await _record_demo_run(db, anon_id, {"ok": False, "reason": code or "failed"})
                return _result(
                    200,
                    {
                        "ok": False,
                        "state": "error",
                        "message": message or "The conversion did not complete on the demo box.",
                        "suggestion": (
                            "Sign up to run this on your own AgiBot data — "
                            "your dataset isn't affected."
                        ),
                    },
                )

            # Success. The validator (5-check `--verify`) JSON is the LAST
            # NDJSON line `SubprocessRunner` already tail-parsed
            # (`subprocess_runner.py:171`, `validate/_report.py:53-72`):
            # {result: PASS|WARN, results:[{name,status,detail}*]}.
            validate = outcome.last_stdout_json or {}
            checks = [
                {
                    "name": c.get("name"),
                    "status": c.get("status"),
                    "detail": c.get("detail"),
                }
                for c in (validate.get("results") or [])
                if isinstance(c, dict)
            ]
            summary = _summarize_output(sess.out_dir)

            await _record_demo_run(
                db,
                anon_id,
                {
                    "ok": True,
                    "validator": validate.get("result"),
                    "episodes": summary.get("episodes"),
                    "frames": summary.get("frames"),
                },
            )

            return _result(
                200,
                {
                    "ok": True,
                    "state": "done",
                    "from_format": _DEMO_FROM,
                    "to_format": _DEMO_TO,
                    "sample": "AgiBot World Beta task 675 (1 episode, minimized in-image sample)",
                    "validator_result": validate.get("result"),
                    "checks": checks,
                    "dataset": summary,
                    "message": "Real conversion complete — validated LeRobot v3 output.",
                },
            )
        except Exception:
            logger.exception("demo conversion errored")
            try:
                await _record_demo_run(db, anon_id, {"ok": False, "reason": "internal"})
            except Exception:
                pass
            return _result(200, _degraded("The demo hit an unexpected snag."))
        finally:
            await asyncio.to_thread(shutil.rmtree, root, ignore_errors=True)
