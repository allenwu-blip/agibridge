# `app/demo_assets/` — in-image demo sample

`agibot_sample/` is the fixed input for the zero-signup "try it" demo
(`POST /api/v1/demo/convert`, impl `app/api/demo.py`). An anonymous Landing
visitor presses one button and watches a **real** AgiBot World -> LeRobot v3
conversion + the real 5-check validator run on this sample — no account.

## What it is

A **minimized REAL AgiBot Beta-675 sample** — *not synthetic data*. It is the
upstream `tests/fixtures/agibot_beta_675_single_ep/` fixture (task 675,
episode 936938) with every `proprio_stats.h5` dataset **truncated to the
first 20 rows** along axis 0. The group tree, dataset keys, dtypes, and attrs
are unchanged; only the row count shrinks, so it stays a structurally valid
1-episode AgiBot Beta proprio file. `task_info_675.json` keeps the real
episode-936938 metadata entry (`action_config` frame indices clamped into the
20-frame range for self-consistency; the converter never reads those fields).

Result: `proprio_stats.h5` is ~45 KB (down from 1.17 MB). The real
`embodied-data` converter + validator accept it unchanged — `convert`
succeeds (1 episode, 20 frames) and `validate` returns `PASS` (3 PASS,
2 SKIP — the 2 SKIPs are inherent to a proprio-only dataset, not the
truncation).

Regenerate with `uv run python scripts/make_demo_sample.py`.

## Why it lives here (under `app/`), NOT under `tests/`

The deployed Hugging Face Space and the Docker image **strip the test
fixtures**:

- `.github/workflows/hf-sync.yml` runs `rm -rf tests/fixtures` before the
  HF push (DR-019: HF's pre-receive hook rejects the binary fixture).
- `.dockerignore` excludes `tests/` entirely from the Docker build context.

So a `tests/fixtures`-rooted demo sample is **absent on the live Space** and
the demo degrades to `ok:false "sample temporarily unavailable"`. Anything
under `app/` is included in both the HF mirror and the Docker image, so this
sample must stay here and **must not be moved under `tests/`**.

(`.dockerignore` excludes `*.md`, so this README itself is not baked into the
runtime image — that is fine, it is documentation for repo readers. The
`.h5` + `.json` payload the demo needs is included.)
