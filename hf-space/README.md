---
title: agibridge
emoji: 🤖
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Convert AgiBot World ↔ LeRobot v3 datasets in your browser. Ephemeral, no accounts.
---

# agibridge — HuggingFace Space

Browser-based AgiBot World ↔ LeRobot v3 converter. Wraps `embodied-data==0.3.1` (MIT, [PyPI](https://pypi.org/project/embodied-data/0.3.1/)) as a thin FastAPI subprocess.

**Files are kept for 30 minutes, then deleted. No accounts, no storage.**

The library is the durable path. For full datasets, batch jobs, and `--max-episodes` slicing:

```bash
pip install embodied-data==0.3.1
```

See the [agibridge GitHub](https://github.com/allenwu-blip/agibridge) for documentation and [embodied-data GitHub](https://github.com/allenwu-blip/embodied-data) for the conversion code itself.

Hobby project by Allen Wu ([@allenwu-blip](https://github.com/allenwu-blip)). MIT licensed. Best effort, no SLA.
