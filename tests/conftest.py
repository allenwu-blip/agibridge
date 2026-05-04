"""Shared fixtures.

Each test gets an isolated AGIBRIDGE_ROOT under tmp_path so SessionStore's
on-disk dirs don't pollute the real /tmp.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point SessionStore's _root_dir() at a per-test tmp dir."""
    root = tmp_path / "agibridge_root"
    root.mkdir()
    monkeypatch.setenv("AGIBRIDGE_ROOT", str(root))
    yield root


@pytest.fixture
def beta_fixture() -> Path:
    """Path to the committed AgiBot Beta task 675 single-episode fixture."""
    p = Path(__file__).parent / "fixtures" / "agibot_beta_675_single_ep"
    assert p.is_dir(), f"fixture missing: {p}"
    assert (p / "task_info_675.json").is_file()
    assert (p / "675" / "936938" / "proprio_stats.h5").is_file()
    return p
