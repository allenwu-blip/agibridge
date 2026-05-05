"""FastAPI app entrypoint for agibridge.

Spec: _workspace/backend-architecture-W1.md v2.2.
Routes per §2 (upload, status, download, health, version, GET /).

This module is the boilerplate `app.main` and is excluded from coverage
requirements (see pyproject.toml `[tool.coverage.run]`); business logic lives
under `app.api.*`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import download, health, status, upload, version
from app.api.session_store import SessionStore
from app.api.subprocess_runner import SubprocessRunner
from app.purge_reaper import PurgeReaper

# Single in-process state — single uvicorn worker per spec §3.
_session_store = SessionStore()
_runner = SubprocessRunner()
_reaper = PurgeReaper(_session_store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan: start PurgeReaper at startup; on shutdown SIGKILL any live
    subprocess process group per spec §4 step 5 + §7 HP-2."""
    # Lazy version log — emit warning once at startup if importlib.metadata
    # disagrees with `embodied-data --version --json` (spec §2.4 v2.2 / C1).
    await health.log_version_drift_once()
    await _reaper.start()
    try:
        yield
    finally:
        await _reaper.stop()
        await _runner.shutdown()


app = FastAPI(
    title="agibridge",
    version="0.0.1",
    description=(
        "Convert AgiBot World and LeRobot v3 datasets in your browser. "
        "Files are kept for 30 minutes, then deleted. No accounts, no storage. "
        "Hobby project under MIT."
    ),
    lifespan=lifespan,
)

# CORS: spec §2 says `*`. No auth means CORS is not a security boundary
# (autonomous decision §11 item 14). Honest rather than performative.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Wire dependency-injection state onto routers
upload.set_state(_session_store, _runner)
status.set_state(_session_store)
download.set_state(_session_store)
health.set_state(_session_store)


app.include_router(upload.router, prefix="/api/v1")
app.include_router(status.router, prefix="/api/v1")
app.include_router(download.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(version.router, prefix="/api/v1")


# Static SPA. The frontend agent will populate app/static/ with the bundle.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount(
        "/assets", StaticFiles(directory=_STATIC_DIR / "assets", check_dir=False), name="assets"
    )


@app.get("/", include_in_schema=False, response_model=None)
async def root() -> JSONResponse | FileResponse:
    """Serve index.html if present; otherwise return a minimal placeholder.

    Per spec §2.5. Frontend-dev produces app/static/index.html.
    """
    index = _STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html")
    # Placeholder until frontend ships. Plain JSON, no F-1 banned words.
    return JSONResponse(
        {
            "ok": True,
            "note": "Frontend bundle not yet shipped. See /docs for the API.",
        }
    )
