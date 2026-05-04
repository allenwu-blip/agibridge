"""GET /api/v1/download/{session_id}. Spec §2.3.

Streaming download with FD-survives-rmtree behavior (spec §4 HP-3): we open
the file *before* PurgeReaper might delete it, and StreamingResponse keeps
that FD open. On Linux the inode persists until the FD closes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.session_store import SessionStore
from app.schemas import State

router = APIRouter()

_session_store: SessionStore | None = None


def set_state(store: SessionStore) -> None:
    global _session_store
    _session_store = store


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": message, "suggestion": None}
    )


@router.get("/download/{session_id}")
async def download(session_id: str) -> StreamingResponse:
    assert _session_store is not None
    sess = await _session_store.get(session_id)
    if sess is None:
        # Distinguishes never-existed (404) from purged-after-finish (410).
        # Per spec §2.3: 410 download_expired requires the session existed and
        # finished but was purged. After PurgeReaper removes it from the store
        # we lose that distinction in v0; the spec treats this as a soft 404.
        raise _err(404, "session_not_found", "We don't know that session. It may have expired.")
    if sess.state != State.done:
        raise _err(409, "not_ready", "The conversion isn't finished yet.")
    if not sess.result_zip.is_file():
        # Race: state.done was written but the zip file is gone — purge mid-flight.
        raise _err(410, "download_expired", "This run has expired and its files are gone.")

    # Open the FD here, BEFORE PurgeReaper rmtree could delete inode (spec §4 HP-3).
    file_handle = sess.result_zip.open("rb")

    def iter_file():
        try:
            while True:
                chunk = file_handle.read(64 * 1024)
                if not chunk:
                    return
                yield chunk
        finally:
            file_handle.close()

    return StreamingResponse(
        iter_file(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{session_id}.zip"',
        },
    )
