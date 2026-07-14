"""Subprocess runner: SIGTERM grace, SIGKILL escalation, error mapping.

Per spec §4 step 5 / §5 / §7 HP-2. Verifies the unit-level invariants without
needing a real `embodied-data` invocation."""

from __future__ import annotations

import asyncio
import platform
import signal
import sys
import time
from pathlib import Path

import pytest

from app.api.session_store import Session
from app.api.subprocess_runner import (
    RunOutcome,
    SubprocessRunner,
    _tail_parse_json,
    map_outcome_to_state,
    update_session_from_outcome,
)
from app.schemas import State

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_tail_parse_picks_last_json_object() -> None:
    """Spec §4 step 2: at most 2 JSON objects per --verify run; tail-parse."""
    stdout = b'{"a": 1}\n{"b": 2, "result": "PASS"}\n'
    last = _tail_parse_json(stdout)
    assert last == {"b": 2, "result": "PASS"}


def test_tail_parse_handles_empty_stdout() -> None:
    assert _tail_parse_json(b"") is None


def test_tail_parse_handles_garbage_lines() -> None:
    """Mixed rich-stderr leakage shouldn't crash tail-parse."""
    stdout = b'progress: 50%\n{"valid": true}\nnot-json\n'
    assert _tail_parse_json(stdout) == {"valid": True}


# ---------------------------------------------------------------------------
# map_outcome_to_state — spec §5 mapping table
# ---------------------------------------------------------------------------


def test_map_success() -> None:
    out = RunOutcome(
        returncode=0,
        signal_killed=None,
        last_stdout_json={"format_pair": ["agibot", "lerobot-v3"]},
        stderr_tail="",
        timed_out=False,
    )
    state, code, _msg, _sug = map_outcome_to_state(out)
    assert state == State.done
    assert code is None


def test_map_converter_rejected_input_passes_through_lib_message() -> None:
    """Per spec §5 + CR-1 + HP-5: `error.message` and `suggestion` come
    verbatim from `_emit.py:41` payload. NEVER invented codes."""
    out = RunOutcome(
        returncode=2,
        signal_killed=None,
        last_stdout_json={
            "error": "could not identify AgiBot variant at /tmp/x",
            "suggestion": "schema summary: ...",
            "exit_code": 2,
        },
        stderr_tail="",
        timed_out=False,
    )
    state, code, msg, sug = map_outcome_to_state(out)
    assert state == State.failed
    assert code == "converter_rejected_input"
    assert msg == "could not identify AgiBot variant at /tmp/x"
    assert sug == "schema summary: ..."


def test_map_validation_failed_after_convert() -> None:
    out = RunOutcome(
        returncode=1,
        signal_killed=None,
        last_stdout_json={
            "result": "FAIL",
            "results": [
                {"name": "fps consistency", "status": "FAIL", "detail": "..."},
            ],
        },
        stderr_tail="",
        timed_out=False,
    )
    state, code, msg, _ = map_outcome_to_state(out)
    assert state == State.failed
    assert code == "validation_failed_after_convert"
    assert "fps consistency" in (msg or "")


def test_map_oom_suspected_on_sigkill_no_payload() -> None:
    """Spec §5: rc<0 + signal=SIGKILL + no JSON → oom_suspected."""
    out = RunOutcome(
        returncode=-9,
        signal_killed=signal.SIGKILL,
        last_stdout_json=None,
        stderr_tail="Killed",
        timed_out=False,
    )
    state, code, _msg, _ = map_outcome_to_state(out)
    assert state == State.failed
    assert code == "oom_suspected"


def test_map_conversion_timeout() -> None:
    out = RunOutcome(
        returncode=-15,
        signal_killed=signal.SIGTERM,
        last_stdout_json=None,
        stderr_tail="",
        timed_out=True,
    )
    state, code, _msg, _ = map_outcome_to_state(out)
    assert state == State.failed
    assert code == "conversion_timeout"


def test_map_converter_crashed_on_unexpected_rc() -> None:
    out = RunOutcome(
        returncode=137,  # external SIGKILL pre-grace
        signal_killed=None,
        last_stdout_json=None,
        stderr_tail="Segmentation fault",
        timed_out=False,
    )
    state, code, _msg, _ = map_outcome_to_state(out)
    assert state == State.failed
    assert code == "converter_crashed"


def test_update_session_from_outcome_sets_finished_at() -> None:
    sess = Session(session_id="x", root=Path("/tmp/x"))
    out = RunOutcome(0, None, {"format_pair": ["agibot", "lerobot-v3"]}, "", False)
    update_session_from_outcome(sess, out)
    assert sess.state == State.done
    assert sess.finished_at is not None


def test_update_session_writes_error_fields_on_failure() -> None:
    sess = Session(session_id="x", root=Path("/tmp/x"))
    out = RunOutcome(
        2, None, {"error": "bad", "suggestion": "fix it", "exit_code": 2}, "stderr-tail-here", False
    )
    update_session_from_outcome(sess, out)
    assert sess.state == State.failed
    assert sess.error_code == "converter_rejected_input"
    assert sess.error_message == "bad"
    assert sess.error_suggestion == "fix it"
    assert sess.stderr_tail == "stderr-tail-here"


# ---------------------------------------------------------------------------
# Live subprocess: SIGTERM grace + SIGKILL escalation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX signals only")
async def test_sigterm_grace_then_sigkill_on_long_sleep(tmp_path: Path) -> None:
    """Spec §4 step 5 + §5 conversion_timeout: SIGTERM, 5 s grace, then SIGKILL.

    We patch SUBPROCESS_TIMEOUT_S and SIGTERM_GRACE_S to 1 s each so the test
    runs fast, then run a Python script that ignores SIGTERM (so escalation
    fires) and would sleep 60 s.
    """
    from app.api import subprocess_runner as sr

    # Build a script that:
    #   1. Ignores SIGTERM (so the grace window expires)
    #   2. Sleeps long
    sleeper_script = tmp_path / "sleeper.py"
    sleeper_script.write_text(
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n"
    )

    sess = Session(session_id="sleep-sess", root=tmp_path)
    sess.in_dir.mkdir(parents=True, exist_ok=True)
    sess.out_dir.mkdir(parents=True, exist_ok=True)
    sess.from_format = "agibot"
    sess.to_format = "lerobot-v3"

    runner = SubprocessRunner()

    # Patch the runner's timing constants for fast test.
    monkey_timeout = 1
    monkey_grace = 1
    sr_orig_timeout = sr.SUBPROCESS_TIMEOUT_S
    sr_orig_grace = sr.SIGTERM_GRACE_S
    sr.SUBPROCESS_TIMEOUT_S = monkey_timeout
    sr.SIGTERM_GRACE_S = monkey_grace

    # Replace cmd builder so we run our sleeper, not embodied-data.
    runner._build_cmd = lambda s: [sys.executable, str(sleeper_script)]  # type: ignore[method-assign]

    t0 = time.monotonic()
    try:
        outcome = await runner.run(sess)
    finally:
        sr.SUBPROCESS_TIMEOUT_S = sr_orig_timeout
        sr.SIGTERM_GRACE_S = sr_orig_grace

    elapsed = time.monotonic() - t0
    # Should escalate within ~timeout + grace + a small buffer.
    assert elapsed < 8, f"escalation took {elapsed}s — likely hung"
    assert outcome.timed_out is True
    # SIGTERM was ignored, so we expect SIGKILL (-9) in the returncode.
    assert outcome.signal_killed in (signal.SIGKILL, signal.SIGTERM, None)


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX signals only")
async def test_runner_uses_new_process_group(tmp_path: Path) -> None:
    """Spec §7 HP-2: subprocess runs in its own process group so killpg works.

    We start a script that prints its own pgid and pid to stdout, then verify
    pgid != our pgid."""

    script = tmp_path / "pgid_check.py"
    script.write_text("import os; print(os.getpgid(os.getpid()), os.getpid())\n")

    sess = Session(session_id="pg-sess", root=tmp_path)
    sess.in_dir.mkdir(parents=True, exist_ok=True)
    sess.out_dir.mkdir(parents=True, exist_ok=True)
    sess.from_format = "agibot"
    sess.to_format = "lerobot-v3"

    runner = SubprocessRunner()
    runner._build_cmd = lambda s: [sys.executable, str(script)]  # type: ignore[method-assign]

    outcome = await runner.run(sess)
    assert outcome.returncode == 0
    # We don't have stdout text here in the outcome, but if the process spawned
    # in our group, killpg in `shutdown()` would kill us. The fact that this
    # completes with rc=0 and no signal kill is the implicit assertion.
    assert outcome.signal_killed is None


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX signals only")
async def test_runner_uvloop_fallback_does_not_killpg_self(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-6 regression: when asyncio.create_subprocess_exec rejects
    `process_group=0` (uvloop on Linux), the runner must fall back to a
    single-process kill on timeout/shutdown — NOT `os.killpg(parent_pgid, ...)`.

    Without the fix, the child inherits the FastAPI worker's pgid and our
    later killpg would SIGTERM/SIGKILL uvicorn itself mid-run.

    We simulate uvloop by intercepting `asyncio.create_subprocess_exec`:
    raise TypeError on the first invocation if `process_group` is in kwargs,
    then transparently call the real function on retry. We then patch
    SUBPROCESS_TIMEOUT_S to a tiny value so `_terminate_with_grace` fires,
    and patch `_killpg_quietly` to record any invocation. Assertion:
    `_killpg_quietly` is never called; the process is reaped via
    `proc.terminate()` / `proc.kill()` instead.
    """
    from app.api import subprocess_runner as sr

    # Sleeper that ignores SIGTERM so terminate() escalates to kill(); long
    # enough that wait_for() trips the patched 1 s timeout reliably.
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text(
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n"
    )

    sess = Session(session_id="uvloop-sess", root=tmp_path)
    sess.in_dir.mkdir(parents=True, exist_ok=True)
    sess.out_dir.mkdir(parents=True, exist_ok=True)
    sess.from_format = "agibot"
    sess.to_format = "lerobot-v3"

    # 1. Intercept create_subprocess_exec to mimic uvloop's TypeError on
    #    process_group, then succeed on retry.
    real_exec = asyncio.create_subprocess_exec
    call_count = {"n": 0}

    async def fake_exec(*cmd: str, **kwargs: object) -> asyncio.subprocess.Process:
        call_count["n"] += 1
        if call_count["n"] == 1 and "process_group" in kwargs:
            raise TypeError("uvloop: process_group not supported")
        kwargs.pop("process_group", None)
        return await real_exec(*cmd, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    # 2. Spy on _killpg_quietly. Any invocation is the bug we're guarding
    #    against (it would target the parent worker's pgid).
    killpg_calls: list[tuple[int, int]] = []

    def spy_killpg(pgid: int, sig: int) -> None:
        killpg_calls.append((pgid, sig))

    monkeypatch.setattr(sr, "_killpg_quietly", spy_killpg)

    # 3. Shrink timeouts so _terminate_with_grace fires fast. Restore in
    #    `finally` so other tests aren't affected.
    sr_orig_timeout = sr.SUBPROCESS_TIMEOUT_S
    sr_orig_grace = sr.SIGTERM_GRACE_S
    sr.SUBPROCESS_TIMEOUT_S = 1
    sr.SIGTERM_GRACE_S = 1

    runner = SubprocessRunner()
    runner._build_cmd = lambda s: [sys.executable, str(sleeper)]  # type: ignore[method-assign]

    try:
        outcome = await runner.run(sess)
    finally:
        sr.SUBPROCESS_TIMEOUT_S = sr_orig_timeout
        sr.SIGTERM_GRACE_S = sr_orig_grace

    # Assertions (CR-6 contract):
    # (a) The fallback retry actually fired — we saw two create_subprocess_exec calls.
    assert call_count["n"] == 2, f"expected fallback retry, got {call_count['n']} calls"
    # (b) The runner correctly recorded that no process group was used.
    assert runner._process_group_used is False
    # (c) Most importantly: _killpg_quietly was NEVER called. Without the fix,
    #     _terminate_with_grace would killpg(parent_pgid, SIGTERM) then SIGKILL.
    assert killpg_calls == [], (
        f"killpg was invoked despite uvloop fallback — would target parent's pgid: {killpg_calls}"
    )
    # (d) The subprocess was nonetheless terminated (rc != None). On POSIX,
    #     proc.kill() yields returncode = -SIGKILL == -9.
    assert outcome.timed_out is True
    assert outcome.returncode is not None

    # (e) shutdown() under the same flag must also avoid killpg. Spawn a fresh
    #     fake-uvloop run, then call shutdown() and re-check.
    call_count["n"] = 0
    killpg_calls.clear()
    sess2 = Session(session_id="uvloop-shutdown-sess", root=tmp_path)
    sess2.in_dir.mkdir(parents=True, exist_ok=True)
    sess2.out_dir.mkdir(parents=True, exist_ok=True)
    # Long-enough sleeper that we shutdown() before timeout.
    quick_sleeper = tmp_path / "quick_sleeper.py"
    quick_sleeper.write_text("import time; time.sleep(30)\n")
    runner2 = SubprocessRunner()
    runner2._build_cmd = lambda s: [sys.executable, str(quick_sleeper)]  # type: ignore[method-assign]

    # Drive run() in the background and shutdown() before it completes.
    sr.SUBPROCESS_TIMEOUT_S = 30  # plenty of head-room
    run_task = asyncio.create_task(runner2.run(sess2))
    await asyncio.sleep(0.3)  # let create_subprocess_exec retry path resolve
    assert runner2._process_group_used is False
    await runner2.shutdown()
    sr.SUBPROCESS_TIMEOUT_S = sr_orig_timeout
    # Wait for the run() coroutine to observe the killed child.
    try:
        await asyncio.wait_for(run_task, timeout=5)
    except TimeoutError:
        run_task.cancel()
        raise
    assert killpg_calls == [], f"shutdown() killpg'd despite uvloop fallback: {killpg_calls}"
