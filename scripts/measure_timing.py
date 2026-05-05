"""Measure end-to-end conversion duration on a fixture and emit a JSON line.

Run this on each fixture and update `app/timing_estimates.py` with the
medians. Per spec §2.2.1: ship populated table from W2 measurements on at
least 5 fixtures.

Usage:
    python scripts/measure_timing.py \\
        --src /path/to/agibot/fixture \\
        --from agibot --to lerobot-v3

Output: one JSON line on stdout suitable for appending to a `.jsonl` log:
    {
        "src": "/path/to/agibot/fixture",
        "from_format": "agibot",
        "to_format": "lerobot-v3",
        "episode_count": 47,
        "duration_seconds": 234.5,
        "exit_code": 0,
        "stderr_tail": "..."
    }

The output matches the bucket key shape (from, to, bucket) used by
`app/timing_estimates.py:_TABLE` so the table can be regenerated programmatically.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.api.timing_estimates import _approx_episode_count, _bucket_for


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--from", dest="from_format", required=True)
    parser.add_argument("--to", dest="to_format", required=True)
    parser.add_argument("--max-episodes", type=int, default=None)
    args = parser.parse_args()

    if not args.src.exists():
        print(f"src does not exist: {args.src}", file=sys.stderr)
        return 2

    ep_count = _approx_episode_count(args.src)
    bucket = _bucket_for(ep_count)

    with tempfile.TemporaryDirectory() as dst:
        # HP-7: use sys.executable rather than literal "python" because macOS
        # dev shells often have only `python3` (no bare `python`); CI / venv
        # environments may also differ. Same reasoning as
        # subprocess_runner.py:_build_cmd.
        cmd = [
            "embodied-data" if shutil.which("embodied-data") else sys.executable,
        ]
        if cmd[0] == sys.executable:
            cmd.extend(["-m", "embodied_data.cli"])
        cmd.extend(
            [
                "--json",
                "convert",
                str(args.src),
                str(dst),
                "--from",
                args.from_format,
                "--to",
                args.to_format,
                "--verify",
            ]
        )
        if args.max_episodes is not None:
            cmd.extend(["--max-episodes", str(args.max_episodes)])

        t0 = time.monotonic()
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration = time.monotonic() - t0

    payload = {
        "src": str(args.src),
        "from_format": args.from_format,
        "to_format": args.to_format,
        "episode_count": ep_count,
        "bucket": bucket,
        "duration_seconds": round(duration, 3),
        "exit_code": result.returncode,
        "stderr_tail": result.stderr[-2048:],
    }
    print(json.dumps(payload))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
