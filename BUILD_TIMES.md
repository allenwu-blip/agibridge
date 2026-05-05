# BUILD_TIMES — measured, not estimated

The spec (`_workspace/backend-architecture-W1.md` v2.2 §9.1) carries baseline
assumptions:

- Cold `docker build`: ~3 min (90 s `uv sync` + 30 s ffmpeg apt + misc)
- Warm rebuild on app code change: ~20 s
- HF Space cold start: ~60–90 s
- uvicorn ready: ~2 s

Per the dispatch brief's standing rule "no fabricated claims", these are
**baselines until measured**. This file replaces the spec's `[ESTIMATE]` markers
with real numbers from the first successful CI run.

## How this file is updated

1. The `docker-build` job in `.github/workflows/ci.yml` records the cold-build
   wall-clock seconds and uploads `build-time-<sha>.md` as a CI artifact.
2. After the first successful run on `main`, Allen runs:
   ```bash
   gh run download --name "build-time-$(git rev-parse main)" --dir _build_times/
   cat _build_times/run.md >> BUILD_TIMES.md
   git add BUILD_TIMES.md && git commit -m "chore: log first measured docker build time"
   ```
3. HF Space cold start + uvicorn-ready time should be measured by the first
   real Space deploy (open the Space build log, copy the stage timings).

## Pending measurements

| Metric                                                 | Spec baseline   | Measured |
| ------------------------------------------------------ | --------------- | -------- |
| `docker build` cold (CI runner, ubuntu-latest)         | ~3 min          | PENDING  |
| `docker build` warm (app code only)                    | ~20 s           | PENDING  |
| HF Space cold start (first hit after >48 h idle)       | ~60–90 s        | PENDING  |
| uvicorn-ready inside container (CMD → 200 on /health)  | ~2 s            | PENDING  |
| First real Beta task convert+validate (47 eps, ~600 MB)| ~3–4 min        | PENDING  |

## Measured runs

(append below; newest first)

<!-- CI-AUTOFILL:START -->
<!-- CI-AUTOFILL:END -->
