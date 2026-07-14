# Dockerfile — agibridge HuggingFace Space (CPU Upgrade, Docker SDK).
#
# Recovered from prior F-1 sprint repo at /Users/allenwu/embodied-data-hosted/agibridge
# git ref: 1cff803c7b0e8182338d32024dc58135ae906688 (branch devops/v0-pipeline)
# Adapted for commercial cut per DR-001 (LOCKED 2026-05-12) and DR-004 (HF Space W1).
#
# Layering follows HF Spaces Docker SDK guidance (UID 1000, port 7860):
#   https://huggingface.co/docs/hub/en/spaces-sdks-docker  accessed 2026-05-14
# and uv's official Docker integration pattern (multi-stage, frozen sync):
#   https://docs.astral.sh/uv/guides/integration/docker/   accessed 2026-05-14

# =============================================================================
# Stage 1: builder — uv + system deps + locked dependency resolution.
# =============================================================================
FROM python:3.12-slim-bookworm AS builder

# uv pinned-by-digest official image; one COPY moves the static binary into PATH.
# Reference: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /uvx /usr/local/bin/

# Build-time system deps. embodied-data==0.3.1 transitive `av` package builds
# Python bindings against system libav headers when no wheel matches; pyarrow
# and h5py also need glibc, so slim-bookworm is the right base (alpine/musl
# would force source rebuilds for all three). Confirmed via pyproject.toml:10-23.
#   - build-essential: C toolchain for native extensions
#   - libavcodec-dev / libavformat-dev / libavutil-dev / libswscale-dev: `av` headers
#   - pkg-config: required by `av` build script to locate libav
# --no-install-recommends + apt cache cleanup per Docker best practices:
#   https://docs.docker.com/build/building/best-practices/#apt-get
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        libavcodec-dev \
        libavformat-dev \
        libavutil-dev \
        libswscale-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# uv configuration for reproducible, hermetic builds inside the container.
# UV_COMPILE_BYTECODE=1 precompiles .pyc → faster cold start in runtime stage.
# UV_LINK_MODE=copy avoids hardlink warnings when /root and target FS differ.
# UV_PYTHON_DOWNLOADS=never forces uv to use the system Python (3.12 from base).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/home/app/.venv

# Build the venv at its FINAL runtime path (/home/app/.venv), NOT /build/.venv.
# virtualenv bakes absolute interpreter paths into every bin/ console-script
# shebang + pyvenv.cfg; relocating across stages (build path != run path)
# breaks `exec bin/uvicorn` → kernel can't find the shebang interpreter and
# reports "no such file or directory" on the script. Same-path build+run keeps
# shebangs valid. (Observed: HF Space RUNTIME_ERROR exit 255 before this fix.)

WORKDIR /build

# Dep-only layer — copy only the lockfile inputs first so this layer is reused
# across every app-code-only rebuild. README.md is required because pyproject.toml:5
# declares `readme = "README.md"` and hatchling reads it during wheel build even
# under `--no-install-project` validation in some uv versions.
COPY pyproject.toml uv.lock README.md ./

# Resolve and install locked deps WITHOUT installing the project itself yet.
# --frozen: fail if uv.lock would change vs pyproject.toml (catches drift).
# --no-dev: skip pytest/ruff/etc. (under [project.optional-dependencies] dev).
# --no-install-project: this layer must not bake in app/ — that's a later layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Now copy the application source and install the project on top of the locked env.
COPY app/ ./app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# =============================================================================
# Stage 2: runtime — minimal image with venv + runtime libs only.
# =============================================================================
FROM python:3.12-slim-bookworm AS runtime

# Runtime-only system deps. The build toolchain (build-essential, *-dev headers)
# is intentionally NOT carried over — multi-stage's whole point.
#   - ffmpeg: required by `av` at runtime for video reencode (lib transitive dep)
#   - libmagic1: required by python-magic for upload MIME magic-byte check
#   - libavcodec59 / libavformat59 / libavutil57 / libswscale6: shared libs for `av`
#   - ca-certificates: outbound HTTPS for boto3 (R2) + future Stripe webhooks
#   - curl: HEALTHCHECK CMD only
# Versions are the bookworm-stable APT defaults; we don't pin to specific ABI
# numbers in the apt line because bookworm's package metadata already enforces
# consistency.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
        libmagic1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces convention: containers run as UID 1000 to match the mount UID.
# Mismatched UIDs surface as EACCES on persistent volume writes.
# Reference: https://huggingface.co/docs/hub/en/spaces-sdks-docker#permissions
RUN useradd --create-home --uid 1000 app
USER app
WORKDIR /home/app

# Copy the resolved virtualenv and project from the builder stage. Owning the
# files as `app:app` avoids EACCES on first run when uvicorn reads from .venv.
COPY --from=builder --chown=app:app /home/app/.venv /home/app/.venv
COPY --from=builder --chown=app:app /build/app /home/app/app

# Activate the venv by putting it first on PATH. We intentionally DO NOT use
# `uv run` at runtime (uv is a build-time tool only; not installed in runtime
# stage). Direct uvicorn invocation cuts ~80ms off cold start vs uv shim.
ENV PATH="/home/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Port 7860 = HF Spaces default exposed port (set via app_port in Space README).
# Reference: https://huggingface.co/docs/hub/en/spaces-sdks-docker#setting-up-docker-spaces
EXPOSE 7860

# HEALTHCHECK against the spec'd liveness endpoint app/api/health.py mounted at
# /api/v1/health by app/main.py:80. 10s start-period mirrors observed cold-start
# floor (FastAPI lifespan startup + embodied-data version probe ~3-5s).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:7860/api/v1/health || exit 1

# Single uvicorn worker — concurrency is GlobalLock-managed inside the app
# (see app/api/session_store.py + the upcoming JobStore D4 wiring). Multiple
# workers would defeat that. Bind 0.0.0.0 so the HF reverse proxy can reach.
# Invoke via the venv's python `-m` (not the bare `uvicorn` console script):
# robust even if the console-script shebang is ever wrong, since it only needs
# the venv python symlink (→ base-image python, present in runtime) + the
# importable uvicorn package in site-packages.
ENTRYPOINT ["/home/app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
