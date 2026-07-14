"""Schema invariants — these doubles as a tripwire if frontend's contract drifts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import (
    ErrorBody,
    HealthResponse,
    State,
    StatusResponse,
    UploadAccepted,
    VersionResponse,
)


def test_state_enum_matches_spec() -> None:
    # Spec §2.2 enum.
    assert {s.value for s in State} == {
        "pending",
        "extracting",
        "running",
        "validating",
        "done",
        "failed",
        "expired",
    }


def test_status_response_progress_clamped_at_99() -> None:
    # Spec §2.2.1: estimated_progress_pct clamped to int 0..99.
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        StatusResponse(
            session_id="x",
            state=State.running,
            expires_at=now,
            estimated_progress_pct=100,
        )


def test_status_response_progress_can_be_null() -> None:
    StatusResponse(
        session_id="x",
        state=State.done,
        expires_at=datetime.now(UTC),
        estimated_progress_pct=None,
    )


def test_upload_accepted_default_note_mentions_30_min() -> None:
    msg = UploadAccepted(
        session_id="x",
        status_url="/api/v1/status/x",
        expires_at=datetime.now(UTC),
    )
    # F-1 framing: every upload response advertises ephemerality.
    assert "30 minutes" in msg.note
    assert "no storage" in msg.note.lower() or "No storage" in msg.note


def test_health_response_required_fields() -> None:
    """D4-harden Check 3b: HealthResponse is the trimmed 2-field contract.
    The dropped fields must NOT be settable model fields anymore."""
    h = HealthResponse(ok=True, version="0.3.1")
    assert h.version == "0.3.1"
    assert h.model_dump() == {"ok": True, "version": "0.3.1"}
    assert set(HealthResponse.model_fields) == {"ok", "version"}


def test_version_response_required_fields() -> None:
    v = VersionResponse(
        agibridge_git_sha="deadbeef",
        embodied_data_version="0.3.1",
        built_at="2026-05-02T00:00:00Z",
    )
    assert v.agibridge_git_sha == "deadbeef"


def test_error_body_optional_fields() -> None:
    e = ErrorBody(code="oom_suspected", message="x")
    assert e.suggestion is None
    assert e.stderr_tail is None
