# agibridge

Convert AgiBot World and LeRobot v3 datasets in your browser. Files are kept
for 30 minutes, then deleted. No accounts, no storage.

Hobby project by Allen Wu (@allenwu-blip). Open source under MIT.
Wraps [embodied-data](https://pypi.org/project/embodied-data/) v0.3.1
unmodified, hard-pinned.

## Status

W1 backend skeleton; spec at `_workspace/backend-architecture-W1.md`.

## Local development

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 7860
```

OpenAPI UI at `http://localhost:7860/docs`.

### Pre-commit

```bash
uv run pre-commit install
```

The F-1 banned-word check sources `_workspace/f1-banned-words.regex` (single
source of truth). Inline `# noqa: f1` exempts a single line where a token is
technically necessary.

## Tests

```bash
uv run pytest
```

The integration test consumes a 1.5 MB AgiBot Beta task 675 single-episode
fixture committed at `tests/fixtures/agibot_beta_675_single_ep/`.

## Architecture

See `_workspace/backend-architecture-W1.md` for the full spec, and
`scripts/measure_timing.py` for how `app/timing_estimates.py` is populated.
