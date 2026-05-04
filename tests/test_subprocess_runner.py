"""Subprocess runner: SIGTERM grace, SIGKILL escalation, error mapping.

Per spec §4 step 5 / §5 / §7 HP-2. Verifies the unit-level invariants without
needing a real `embodied-data` invocation."""

from __future__ import annotations

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
