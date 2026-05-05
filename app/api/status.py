"""GET /api/v1/status/{session_id}. Spec §2.2 + §2.2.1 (estimated_progress_pct)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from app.api.session_store import Session, SessionStore
from app.schemas import ErrorBody, State, StatusResponse

router = APIRouter()

_session_store: SessionStore | None = None


def set_state(store: SessionStore) -> None:
    global _session_store
    _session_store = store


@router.get("/status/{session_id}", response_model=StatusResponse)
async def get_status(session_id: str) -> StatusResponse:
    assert _session_store is not None
    sess = await _session_store.get(session_id)
    if sess is None:
        # Spec §2.2: 404 covers both never-existed and purged. Indistinguishable
        # on purpose so prior session ids don't leak.
        raise HTTPException(
            status_code=404,
            detail={
                "code": "session_not_found",
                "message": "We don't know that session. It may have expired.",
            },
        )
    return _to_response(sess)


def _to_response(sess: Session) -> StatusResponse:
    error_body: ErrorBody | None = None
    if sess.state == State.failed:
        error_body = ErrorBody(
            code=sess.error_code or "unknown",
            message=sess.error_message or "Conversion failed.",
            suggestion=sess.error_suggestion,
            stderr_tail=sess.stderr_tail,
        )

    download_url = f"/api/v1/download/{sess.session_id}" if sess.state == State.done else None

    return StatusResponse(
        session_id=sess.session_id,
        state=sess.state,
        started_at=sess.started_at,
        finished_at=sess.finished_at,
        expires_at=sess.expires_at,
        error=error_body,
        download_url=download_url,
        estimated_progress_pct=_estimated_progress_pct(sess),
    )


def _estimated_progress_pct(sess: Session) -> int | None:
    """Spec §2.2.1: int 0..99 while state in {running, validating}; else null.

    Clamped at 99 — only `state == done` should imply 100% to the user.
    """
    if sess.state not in (State.running, State.validating):
        return None
    if sess.started_at is None or sess.estimated_total_s is None or sess.estimated_total_s <= 0:
        return 0
    elapsed = (datetime.now(UTC) - sess.started_at).total_seconds()
    pct = int(elapsed / sess.estimated_total_s * 100)
    if pct < 0:
        return 0
    if pct > 99:
        return 99
    return pct
