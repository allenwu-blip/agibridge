#!/usr/bin/env python3
"""One-off: build the in-image zero-signup demo sample by TRUNCATING the real
AgiBot Beta-675 fixture — never synthesizes proprio values.

Why this exists
---------------
`app/api/demo.py` runs a real AgiBot World -> LeRobot v3 conversion for the
anonymous "try it" demo. It used to read the sample from
`tests/fixtures/agibot_beta_675_single_ep/`, but that path is stripped from
the deployed HF Space (`.github/workflows/hf-sync.yml` `rm -rf tests/fixtures`)
and from the Docker image (`.dockerignore` excludes `tests/`). So the live
demo had no sample. The fix ships a minimized REAL sample under `app/` (which
survives both paths) at `app/demo_assets/agibot_sample/`.

What it does
------------
Opens the real 1.17 MB `proprio_stats.h5` and writes a copy with every dataset
sliced to the first N rows along axis 0 — same group tree, keys, dtypes, and
attrs, just fewer rows. The result stays a structurally valid 1-episode AgiBot
Beta proprio file (`state/joint/position` is still `(N, 14)` float64, etc.) so
the real `embodied-data` converter + 5-check validator accept it unchanged.

`(0,)`-shaped datasets (empty in the upstream capture) are copied as-is — they
have no rows to truncate and the converter's `_read_optional_2d` already
tolerates them.

`task_info_675.json` is a list of 399 per-episode metadata dicts. The converter
only reads `data[0]['task_name']` (`agibot_beta_to_lerobot.py:_resolve_task_name
_from_file`); it never reads `action_config`/`episode_id`. We keep ONLY the
entry for the episode we actually ship (936938) so the JSON is internally
consistent with the shipped `.h5`, and clamp its `action_config` frame indices
into the truncated [0, N) range so the file stays self-consistent.

This is a build-time script, not runtime code. Run once:
    uv run python scripts/make_demo_sample.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np

# Number of frames (rows) to keep. The converter needs N >= 2 (first-diff
# action) and the validator's timestamp check needs >= 2 rows/episode; 20 is
# comfortably above both while keeping the file at tens of KB.
N_ROWS = 20

# Episode whose proprio_stats.h5 we ship (the one real .h5 in the fixture).
SHIP_EPISODE_ID = 936938

REPO = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO / "tests" / "fixtures" / "agibot_beta_675_single_ep"
DST_ROOT = REPO / "app" / "demo_assets" / "agibot_sample"


def _truncate_h5(src_h5: Path, dst_h5: Path, n: int) -> None:
    """Copy src_h5 -> dst_h5 with every dataset sliced to the first n rows.

    Preserves the full group tree, dataset keys, dtypes, and all attrs.
    Empty (0,)-shaped datasets are copied verbatim.
    """
    with h5py.File(src_h5, "r") as fin, h5py.File(dst_h5, "w") as fout:
        # Root attrs (none on this file, but copy for fidelity).
        for k, v in fin.attrs.items():
            fout.attrs[k] = v

        def _visit(name: str, obj: h5py.HLObject) -> None:
            if isinstance(obj, h5py.Group):
                grp = fout.require_group(name)
                for k, v in obj.attrs.items():
                    grp.attrs[k] = v
            elif isinstance(obj, h5py.Dataset):
                data = np.asarray(obj[()])
                if data.ndim >= 1 and data.shape[0] > 0:
                    data = data[:n]
                # 0-row datasets and scalars pass through unchanged.
                ds = fout.create_dataset(name, data=data, dtype=obj.dtype)
                for k, v in obj.attrs.items():
                    ds.attrs[k] = v

        fin.visititems(_visit)


def _truncate_task_info(src_json: Path, dst_json: Path, n: int) -> None:
    """Keep only the shipped episode's entry; clamp its action frames to [0, n)."""
    data = json.loads(src_json.read_text())
    if not isinstance(data, list):
        raise SystemExit(f"unexpected task_info shape: {type(data).__name__}")

    entry = next((e for e in data if e.get("episode_id") == SHIP_EPISODE_ID), None)
    if entry is None:
        raise SystemExit(f"episode {SHIP_EPISODE_ID} not found in {src_json}")

    # Clamp action_config frame indices into the truncated range so the JSON
    # stays internally consistent with the N-row .h5. The converter does not
    # read these fields, but a self-consistent file is the honest artifact.
    label = entry.get("label_info", {})
    clamped = []
    for ac in label.get("action_config", []):
        sf = min(int(ac.get("start_frame", 0)), n - 1)
        ef = min(int(ac.get("end_frame", 0)), n)
        if ef <= sf:
            # Segment falls entirely past the truncation point — drop it.
            continue
        clamped.append({**ac, "start_frame": sf, "end_frame": ef})
    if clamped:
        label["action_config"] = clamped
    else:
        # Keep at least one segment spanning the whole truncated clip.
        label["action_config"] = [
            {
                "start_frame": 0,
                "end_frame": n,
                "action_text": label.get("action_config", [{}])[0].get("action_text", ""),
                "skill": label.get("action_config", [{}])[0].get("skill", ""),
            }
        ]
    entry["label_info"] = label

    dst_json.write_text(json.dumps([entry], indent=1) + "\n")


def main() -> int:
    if not SRC_ROOT.is_dir():
        raise SystemExit(f"source fixture missing: {SRC_ROOT}")

    src_h5 = SRC_ROOT / str(675) / str(SHIP_EPISODE_ID) / "proprio_stats.h5"
    src_json = SRC_ROOT / "task_info_675.json"
    if not src_h5.is_file() or not src_json.is_file():
        raise SystemExit(f"expected {src_h5} + {src_json}")

    dst_h5 = DST_ROOT / str(675) / str(SHIP_EPISODE_ID) / "proprio_stats.h5"
    dst_json = DST_ROOT / "task_info_675.json"
    dst_h5.parent.mkdir(parents=True, exist_ok=True)

    _truncate_h5(src_h5, dst_h5, N_ROWS)
    _truncate_task_info(src_json, dst_json, N_ROWS)

    src_size = src_h5.stat().st_size
    dst_size = dst_h5.stat().st_size
    print(f"h5:   {src_size:>9,} B -> {dst_size:>9,} B  ({src_h5} -> {dst_h5})")
    print(
        f"json: {src_json.stat().st_size:>9,} B -> {dst_json.stat().st_size:>9,} B  "
        f"({src_json} -> {dst_json})"
    )
    print(f"kept first {N_ROWS} rows; shipped episode {SHIP_EPISODE_ID}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
