# agibridge

> AgiBot World and LeRobot v3 use different schemas. Converting between them is currently a one-off script per researcher. **agibridge** wraps the `embodied-data` library as one command plus a 5-check validator — runnable locally (recommended for serious work) or in a browser (hosted demo for quick checks).

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![embodied-data](https://img.shields.io/pypi/v/embodied-data.svg?label=embodied-data)](https://pypi.org/project/embodied-data/0.3.1/)

## Run locally (recommended)

```bash
pip install embodied-data==0.3.1
embodied-data convert ./agibot_root /tmp/v3_out --from agibot --to lerobot-v3 --verify
embodied-data validate /tmp/v3_out
```

That's the whole thing. `--verify` runs the 5-check validator (schema, fps, timestamp monotonicity, action-dim consistency, frame-video alignment) inline. See [QUICKSTART.md](QUICKSTART.md) for a 30-second end-to-end check using `lerobot/pusht`.

For full datasets, batch jobs, custom flags, and `--max-episodes` slicing: stay on the CLI. The library is the durable path.

## Try in a browser (hosted demo)

For researchers without a local Python setup, agibridge is also a thin web wrapper around the same library, hosted on a free HuggingFace Space.

- Upload an AgiBot or LeRobot v3 archive (max 800 MB compressed / 1.5 GB uncompressed)
- Pick the conversion direction
- Download the converted + validated output

**Files are kept for 30 minutes, then deleted. No accounts, no storage.** Demo URL: `<DOMAIN_TBD>` (will resolve once DNS is confirmed).

The hosted demo runs `embodied-data==0.3.1` unmodified as a subprocess. The `/api/v1/health` endpoint surfaces the lib version so the paper trail is auditable.

## What it doesn't do

- No AgiBot Beta multi-camera (`head_color` only on the hosted demo; the other 6 cameras are silently dropped per [`docs/schema/beta.md`](https://github.com/allenwu-blip/embodied-data/blob/main/docs/schema/beta.md))
- No cross-embodiment action-space retargeting (explicit non-goal)
- No Chinese prompt embedding (explicit non-goal)
- No persistent dataset hosting, sharing, or accounts on the demo
- No SLA, no uptime promise — best effort, hobby framing

## Upstream issues this addresses

- AgiBot-World [#18](https://github.com/OpenDriveLab/AgiBot-World/issues/18) — `task_info_*.json` lookup ambiguity
- AgiBot-World [#124](https://github.com/OpenDriveLab/AgiBot-World/issues/124) — Beta vs Alpha schema divergence
- AgiBot-World [#149](https://github.com/OpenDriveLab/AgiBot-World/issues/149) — proprio HDF5 key drift across batches
- lerobot [#2158](https://github.com/huggingface/lerobot/issues/2158) — v2 ↔ v3 episode-index incompatibility

## How to file bugs

Open an issue on the [embodied-data GitHub](https://github.com/allenwu-blip/embodied-data/issues) — that's where the actual conversion code lives, and that's where contributors will see it. Include:

- The `embodied-data` version (`pip show embodied-data` or `embodied-data --version`)
- The CLI command you ran (or, for the hosted demo, the session_id from your browser URL)
- A minimal repro — a redacted dataset slice if possible, or the stderr tail

If a hosted-demo session failed and you need someone to look at logs, paste the session_id; logs are kept ~7 days on HuggingFace Space's log viewer. For schema-shape edge cases, the embodied-data issue tracker is the right venue — researchers helping researchers, not a support queue.

## Sponsor / follow

- Code (library): [github.com/allenwu-blip/embodied-data](https://github.com/allenwu-blip/embodied-data)
- Code (this wrapper): [github.com/allenwu-blip/agibridge](https://github.com/allenwu-blip/agibridge)
- PyPI: [pypi.org/project/embodied-data](https://pypi.org/project/embodied-data/)
- Sponsor: `<GITHUB_SPONSORS_URL>` (TBD)
- Discord: `<DISCORD_INVITE_URL>` (TBD)

Contributors welcome on either repo. Issues, PRs, and conversion edge cases all valued — open an issue first if the fix is non-trivial so we can sanity-check direction before code.

## Footer

Hobby project by Allen Wu ([@allenwu-blip](https://github.com/allenwu-blip)). Open source under MIT. Wraps `embodied-data` v0.3.1, unmodified. Not affiliated with HuggingFace, OpenDriveLab, or any organization.
