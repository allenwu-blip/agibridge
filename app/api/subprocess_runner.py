"""Subprocess invocation of `embodied-data --json convert ... --verify`.

Spec §4 (lifecycle), §5 (error taxonomy), §6 HP-1 (RLIMIT_AS), §7 HP-2
(process_group), §10 item 11 (no print of user content).

Key lib citations the wrapper depends on:
- `_emit.py:32-46` defines the JSON error contract `{error, suggestion, exit_code}`.
- `convert/__init__.py:38-43` silences rich-stderr in --json mode (so stderr is
  mostly empty in success / structured-error paths; meaningful only on signal kill).
- `convert/__init__.py:386-396, 502-513, 574-585` are the success-emit sites
  (one terminal JSON object per --json run).
- `convert/__init__.py:75-93` defines `_run_post_convert_verify`; the lerobot-v3
  -> agibot reverse pair is a no-op (lines 82-88).
- `validate/_report.py:53-72` defines the validate JSON envelope; `result: "FAIL"`
  + exit 1 distinguishes `validation_failed_after_convert` from a pure crash.
- `cli.py:124-127` declares `--max-episodes` and `cli.py:136-142` declares
  `--verify`.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.api.session_store import Session
from app.schemas import State

# Spec §6: 25-min wall clock. Below the 30-min purge (§4).
SUBPROCESS_TIMEOUT_S = 1500
# Spec §4 step 5 + §7 HP-2: 5 s grace after SIGTERM before SIGKILL.
SIGTERM_GRACE_S = 5
# Spec §6 HP-1: prlimit --as=12884901888 (12 GB virtual). Honest about VSZ.
PRLIMIT_AS_BYTES = 12 * 1024 * 1024 * 1024

# Last-N bytes of stderr we keep on the failure record (spec §5).
STDERR_TAIL_BYTES = 4096


@dataclass
class RunOutcome:
    """Result of a `embodied-data convert` run, mapped to wrapper-side fields.

    Field meaning matches spec §5 mapping table; the actual code-string is
    chosen in `map_outcome_to_state()` based on `returncode` / `signal_killed`
    / `last_stdout_json` shape.
    """

    returncode: int | None
    signal_killed: int | None  # signal number if signal-killed, else None
    last_stdout_json: dict[str, Any] | None
    stderr_tail: str
    timed_out: bool


class SubprocessRunner:
    """Owns the at-most-one live subprocess. Exposes `run()` for the upload
    handler's background task; tracks the process group id so the lifespan
    shutdown hook can SIGKILL the whole group (spec §4 step 5)."""

    def __init__(self) -> None:
        self._live_proc: asyncio.subprocess.Process | None = None
        self._live_pgid: int | None = None
        # HP-2 / CR-6: True iff the live subprocess was actually spawned with
        # `process_group=0` (i.e. its own pgid). Under uvloop the kwarg is
        # rejected and dropped on retry, so the child inherits the parent's
        # pgid; killpg(parent_pgid, ...) would then SIGKILL the FastAPI worker.
        # Spec §7 HP-2 + §4 step 5 lifespan teardown contract: only killpg
        # when this flag is True; otherwise fall back to single-process
        # terminate/kill.
        self._process_group_used: bool = False
        self._lock = asyncio.Lock()

    async def shutdown(self) -> None:
        """FastAPI lifespan teardown.

        Spec §7 HP-2 + §4 step 5: SIGKILL the subprocess group on graceful
        shutdown so no orphan converter survives. CR-6 gating: only signal
        the process group when we actually placed the child in its own pgid;
        under the uvloop fallback the child inherited the parent's pgid and
        killpg would target the FastAPI worker itself."""
        async with self._lock:
            if self._process_group_used and self._live_pgid is not None:
                _killpg_quietly(self._live_pgid, signal.SIGKILL)
            elif self._live_proc is not None:
                # Single-process fallback: orphan blast radius bounded by
                # /tmp wipe on container restart per spec §7 HP-2 honest fallback.
                try:
                    self._live_proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            self._live_proc = None
            self._live_pgid = None
            self._process_group_used = False

    async def run(self, sess: Session) -> RunOutcome:
        """Spawn `embodied-data --json convert ... --verify` in a new process
        group. Capture stdout fully (terminal JSON object); keep last 4 KB of
        stderr for failure diagnostics."""
        cmd = self._build_cmd(sess)
        # Spawn under a new process group so we can killpg() the whole tree
        # (spec §7 HP-2). `process_group=0` is supported by stdlib asyncio
        # in Python 3.11+ — see
        # https://docs.python.org/3.12/library/asyncio-subprocess.html#asyncio.create_subprocess_exec
        # — but uvloop (uvicorn[standard]'s default loop on Linux) rejects
        # the kwarg with TypeError. CR-6: when uvloop forces process_group=0
        # to be dropped, fallback to single-process kill to avoid killing the
        # parent's pgid (which the child would inherit). Track which path
        # actually fired in self._process_group_used so _terminate_with_grace
        # and shutdown gate killpg correctly.
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": str(sess.root),
        }
        if platform.system() != "Windows":
            kwargs["process_group"] = 0

        process_group_used = False
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
            # First try succeeded with process_group=0 (or it wasn't requested
            # on Windows, in which case there's no group to manage anyway).
            process_group_used = "process_group" in kwargs
        except (TypeError, ValueError):
            # uvloop does not accept process_group; retry without it. The
            # child will inherit the parent's pgid — DO NOT killpg later.
            kwargs.pop("process_group", None)
            proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
            process_group_used = False

        async with self._lock:
            self._live_proc = proc
            self._process_group_used = process_group_used
            if process_group_used:
                try:
                    self._live_pgid = os.getpgid(proc.pid) if proc.pid else None
                except (ProcessLookupError, OSError):
                    self._live_pgid = None
            else:
                self._live_pgid = None

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=SUBPROCESS_TIMEOUT_S
            )
        except TimeoutError:
            timed_out = True
            stdout_bytes, stderr_bytes = await self._terminate_with_grace(proc)

        async with self._lock:
            self._live_proc = None
            self._live_pgid = None
            self._process_group_used = False

        rc = proc.returncode
        signal_killed: int | None = None
        if rc is not None and rc < 0:
            # POSIX: returncode = -signal (e.g. -9 == SIGKILL). spec §5.
            signal_killed = -rc

        last_json = _tail_parse_json(stdout_bytes or b"")
        stderr_tail = (stderr_bytes or b"")[-STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")

        return RunOutcome(
            returncode=rc,
            signal_killed=signal_killed,
            last_stdout_json=last_json,
            stderr_tail=stderr_tail,
            timed_out=timed_out,
        )

    async def _terminate_with_grace(self, proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        """Send SIGTERM, wait SIGTERM_GRACE_S, then SIGKILL.

        Per spec §4 step 5 + §5 conversion_timeout. CR-6: when
        self._process_group_used is True, signal the whole process group via
        killpg() so converter children (e.g. ffmpeg shelled from av) die with
        the parent. When False (uvloop fallback), the child inherits the
        FastAPI worker's pgid; killpg would SIGTERM/SIGKILL uvicorn itself —
        instead use single-process proc.terminate() / proc.kill(). Returns
        whatever stdout/stderr was buffered."""
        if self._process_group_used:
            try:
                pgid = os.getpgid(proc.pid) if proc.pid else None
            except (ProcessLookupError, OSError):
                pgid = None
            if pgid is not None:
                _killpg_quietly(pgid, signal.SIGTERM)
        else:
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=SIGTERM_GRACE_S
            )
            return stdout_bytes, stderr_bytes
        except TimeoutError:
            pass

        if self._process_group_used:
            try:
                pgid = os.getpgid(proc.pid) if proc.pid else None
            except (ProcessLookupError, OSError):
                pgid = None
            if pgid is not None:
                _killpg_quietly(pgid, signal.SIGKILL)
        else:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=2)
        except TimeoutError:
            stdout_bytes, stderr_bytes = b"", b""
        return stdout_bytes, stderr_bytes

    def _build_cmd(self, sess: Session) -> list[str]:
        """Build the subprocess argv. Use `prlimit` on Linux (best-effort
        VSZ cap per HP-1); on macOS / Windows fall back to plain invocation
        (HF Space runs Linux, so the cap applies in prod)."""
        base = [
            "embodied-data",
            "--json",
            "convert",
            str(sess.in_dir / "dataset"),
            str(sess.out_dir),
            "--from",
            sess.from_format or "agibot",
            "--to",
            sess.to_format or "lerobot-v3",
            "--verify",  # cli.py:136-142
        ]
        # Tier-gating (Story #8, dispatches/D4_specs.md:149). Free tier sets
        # sess.max_episodes=1 -> `--max-episodes 1` (cli.py:124-127). This is
        # ADDITIVE: when sess.max_episodes is None the argv is byte-identical
        # to the DR-019-verified command, so conversion correctness is
        # untouched; the flag only ever truncates input the lib already
        # supports truncating.
        if sess.max_episodes is not None:
            base += ["--max-episodes", str(sess.max_episodes)]
        # `embodied-data` shim isn't on PATH in some test envs; fall back to
        # `<sys.executable> -m embodied_data.cli`. Use `sys.executable` rather
        # than literal `python` because macOS dev shells often have only
        # `python3` (no bare `python`); CI / venv environments may also differ.
        if not shutil.which("embodied-data"):
            base = [sys.executable, "-m", "embodied_data.cli"] + base[1:]

        # prlimit is Linux-only. Best-effort guard per spec §6 HP-1.
        if platform.system() == "Linux" and shutil.which("prlimit"):
            return ["prlimit", f"--as={PRLIMIT_AS_BYTES}", "--"] + base
        return base


def _killpg_quietly(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        # Group already gone or unreachable; nothing to do.
        pass


def _tail_parse_json(stdout: bytes) -> dict[str, Any] | None:
    """Find the last newline-terminated JSON object in stdout.

    `embodied-data --json` emits at most 2 NDJSON lines per --verify run:
    one from convert (`convert/__init__.py:386, 502, 574`) and one from
    validate (`validate/_report.py:53-72`). On error there is exactly one
    `_emit.py:41` payload. Tail-parse handles all three cases."""
    if not stdout:
        return None
    text = stdout.decode("utf-8", errors="replace")
    last: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            last = obj
    return last


def map_outcome_to_state(outcome: RunOutcome) -> tuple[State, str | None, str | None, str | None]:
    """Translate (returncode, signal, last_json) into spec §5's wrapper code.

    Returns (terminal_state, code, message, suggestion). `terminal_state` is
    either `State.done` or `State.failed`. Caller writes the result onto the
    Session.
    """
    rc = outcome.returncode
    last = outcome.last_stdout_json or {}
    sig = outcome.signal_killed

    # Timeout: per §5 conversion_timeout. We mark the runner as timed_out
    # before assessing rc (rc may be -SIGTERM or -SIGKILL after our grace).
    if outcome.timed_out:
        return (
            State.failed,
            "conversion_timeout",
            "The run took longer than 25 minutes and was stopped.",
            "Smaller slices via `--max-episodes` work; full Beta tasks are best run locally.",
        )

    # SIGKILL with no JSON payload → likely OOM (spec §5 oom_suspected).
    # HF container limit is 16 GB; OOM-killer SIGKILLs and we get rc < 0 sig 9.
    if sig is not None:
        if sig == signal.SIGKILL and not last:
            return (
                State.failed,
                "oom_suspected",
                "The run was stopped, most likely because it exceeded the memory "
                "limit on this hosted environment.",
                "Run the CLI locally for full datasets: `pip install embodied-data`.",
            )
        return (
            State.failed,
            "converter_crashed",
            f"The converter was killed by signal {sig}.",
            "Please consider opening an issue on the embodied-data GitHub.",
        )

    # Success: rc == 0. Validate may have emitted a separate JSON; the convert
    # JSON has a `format_pair` key (convert/__init__.py:390, 506, 578). The
    # validate JSON has `result` in {PASS, WARN, FAIL} (`_report.py:65-72`).
    if rc == 0:
        # If --verify was on and the LAST JSON happens to be the validate
        # envelope with WARN/PASS, that's fine. We only fail on result=FAIL.
        if last.get("result") == "FAIL":
            # Should be unreachable: lib raises SystemExit(1) on FAIL per
            # `validate/__init__.py:69-70` (cited in spec §4 step 2).
            return (
                State.failed,
                "validation_failed_after_convert",
                "Conversion finished but the 5-check validator flagged the output.",
                None,
            )
        return State.done, None, None, None

    # rc == 1 with validate FAIL payload → validation_failed_after_convert.
    if rc == 1 and last.get("result") == "FAIL":
        # Compose detail list from `_report.py:68` results array.
        fail_names = [
            r.get("name", "?") for r in (last.get("results") or []) if r.get("status") == "FAIL"
        ]
        msg = (
            "Conversion finished but the 5-check validator flagged the output. "
            f"Failing checks: {fail_names}"
        )
        return State.failed, "validation_failed_after_convert", msg, None

    # rc == 2 → converter_rejected_input. The lib passes through `error` and
    # `suggestion` per `_emit.py:41`. NEVER invent error.code from substring
    # match — spec §5 explicit rule (CR-1, HP-5).
    if rc == 2:
        return (
            State.failed,
            "converter_rejected_input",
            str(last.get("error") or "The converter rejected the input."),
            last.get("suggestion"),
        )

    # Any other non-zero rc (3, 137 from external SIGKILL pre-grace, etc.) →
    # converter_crashed. Stderr tail is attached separately on the Session.
    return (
        State.failed,
        "converter_crashed",
        "The converter ran into an unexpected error.",
        "Please consider opening an issue on the embodied-data GitHub.",
    )


def update_session_from_outcome(sess: Session, outcome: RunOutcome) -> None:
    """Mutate `sess` in place per the wrapper's error mapping. Caller writes
    `status.json` afterward."""
    state, code, message, suggestion = map_outcome_to_state(outcome)
    sess.state = state
    sess.finished_at = datetime.now(UTC)
    if state == State.failed:
        sess.error_code = code
        sess.error_message = message
        sess.error_suggestion = suggestion
        sess.stderr_tail = outcome.stderr_tail or None
