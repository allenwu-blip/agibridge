"""Estimated total subprocess duration lookup. Spec §2.2.1.

`estimated_progress_pct = min(99, int(elapsed_s / estimated_total_s * 100))`.
Frontend renders with explicit "(estimate)" annotation; never claimed as the
lib's per-episode progress (the lib has none; convert/__init__.py:38-43).

Buckets are by (from_format, to_format, episode_count_bucket). Episode count
is approximated from input directory file structure — cheap heuristic.

[ESTIMATE -- to be measured in W2] All numbers below are placeholders; replace
with measurements from `scripts/measure_timing.py` against 5+ fixtures.
"""

from __future__ import annotations

from pathlib import Path

# Bucket boundaries by approx episode count. Tuple is (from, to, bucket_key).
# bucket_key is 'small' (<=5 ep), 'medium' (<=50 ep), 'large' (>50 ep).
# [ESTIMATE -- to be measured in W2]
_TABLE: dict[tuple[str, str, str], int] = {
    ("agibot", "lerobot-v3", "small"): 60,  # [ESTIMATE]
    ("agibot", "lerobot-v3", "medium"): 240,  # [ESTIMATE]
    ("agibot", "lerobot-v3", "large"): 900,  # [ESTIMATE]
    ("lerobot-v3", "agibot", "small"): 30,  # [ESTIMATE]
    ("lerobot-v3", "agibot", "medium"): 120,  # [ESTIMATE]
    ("lerobot-v3", "agibot", "large"): 600,  # [ESTIMATE]
}

# Default if pair / bucket not in table — keeps wrapper conservative.
# [ESTIMATE -- to be measured in W2]
_DEFAULT_TOTAL_S = 300


def _bucket_for(episode_count: int) -> str:
    if episode_count <= 5:
        return "small"
    if episode_count <= 50:
        return "medium"
    return "large"


def _approx_episode_count(in_dir: Path) -> int:
    """Best-effort: count likely episode dirs / proprio_state*.h5 files.

    For AgiBot Beta: episode dirs under <task>/<ep_id>/ contain proprio_stats.h5.
    For LeRobot v3 source: top-level meta/info.json declares total_episodes
    (we don't parse it here; just count *.parquet files under data/ as a proxy).
    Cheap heuristic only — accuracy doesn't matter much because the buckets
    are coarse.
    """
    if not in_dir.exists():
        return 1
    h5_count = sum(1 for _ in in_dir.rglob("proprio_stat*.h5"))
    parquet_count = sum(1 for _ in in_dir.rglob("*.parquet"))
    return max(1, h5_count, parquet_count)


def estimate_total_seconds(*, from_format: str, to_format: str, in_dir: Path) -> int:
    """Return the bucket lookup, or _DEFAULT_TOTAL_S if not found."""
    ep_count = _approx_episode_count(in_dir)
    bucket = _bucket_for(ep_count)
    return _TABLE.get((from_format, to_format, bucket), _DEFAULT_TOTAL_S)
