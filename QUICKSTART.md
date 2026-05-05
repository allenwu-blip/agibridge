# QUICKSTART

End-to-end check in ~30 seconds against `lerobot/pusht` (no HuggingFace gating, no AgiBot access required).

**Prerequisites**: Python 3.12+, `pip`, ~150 MB disk for the dataset.

## 3 commands

```bash
pip install embodied-data==0.3.1

hf download lerobot/pusht --repo-type dataset --local-dir ./pusht

embodied-data validate ./pusht
```

## What you should see

The first command installs `embodied-data` and its dependencies (h5py, pyarrow, av, ffmpeg bindings, ~30 s on a fresh venv).

The second command pulls the LeRobot v3 `pusht` dataset (~150 MB, 206 episodes, 25 650 frames) into `./pusht/`. The HuggingFace CLI ships with the install above as `hf` (the older `huggingface-cli` alias also works on most installs).

The third command runs the 5-check validator and prints a Rich-rendered table. Expected output ends with:

```
Result: PASS
```

If any check fails, the row shows `FAIL` with a one-line detail and the process exits non-zero — that's the contract `agibridge` and CI hooks rely on.

## Next steps

- Convert real AgiBot Beta data: `embodied-data convert ./agibot_root /tmp/v3_out --from agibot --to lerobot-v3 --verify` (request gated access on the [AgiBotWorld-Beta](https://huggingface.co/datasets/agibot-world/AgiBotWorld-Beta) page first).
- Inspect a dataset: `embodied-data inspect ./pusht --summary`.
- Browse the full CLI: `embodied-data --help`.
- For a no-install browser run, the hosted demo is at `agibridge.dev` (DNS pending Vercel + CNAME wiring; not yet live). Files are kept for 30 minutes, then deleted. No accounts, no storage.

## If something breaks

Open an issue on the [embodied-data GitHub](https://github.com/allenwu-blip/embodied-data/issues). Include the `embodied-data --version` output and the exact command you ran. Researchers helping researchers — contributors welcome.
