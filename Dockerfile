# Dockerfile — agibridge HuggingFace Space (CPU Basic, Docker SDK).
#
# Source-grounded layering per spec _workspace/backend-architecture-W1.md v2.2 §9.1.
# Permissions / non-root user pattern per HF Spaces Docker docs:
#   https://huggingface.co/docs/hub/spaces-sdks-docker#permissions
# (HF Spaces containers run as UID 1000; create the user before any COPY.)
#
# Port 7860 is the HF Space default exposed by `app_port: 7860` in hf-space/README.md.
# See https://huggingface.co/docs/hub/spaces-sdks-docker#setting-up-docker-spaces
# (the "default exposed port 7860" sentence and the YAML example in that section).

# ---- Layer 1: base image (spec §9.1 step 1) -----------------------------------
# python:3.12-slim-bookworm pinned by major+minor. requires-python in
# pyproject.toml is ">=3.12"; A3.3 locks CI to 3.12 only, so the runtime tracks.
FROM python:3.12-slim-bookworm

# ---- Layer 2: system deps (spec §9.1 step 2) ----------------------------------
# - ffmpeg: required by `av` (the lib's pyproject.toml:16 dep) for h264 reencode.
# - libmagic1: required by python-magic for the upload MIME magic-byte check
#              (spec §7 "MIME spoofing" mitigation).
# - ca-certificates: required so subprocess HTTPS / pip TLS works during build.
# --no-install-recommends keeps the image lean per Docker official best practice
#   (https://docs.docker.com/build/building/best-practices/#apt-get).
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
        libmagic1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---- Layer 3: install uv (build-time tool only) -------------------------------
# uv is the spec's chosen lockfile mechanism (spec §9.1 step 4 / HP-7).
# Pinned via the official installer; copied as a single binary, not into the
# global site-packages. https://docs.astral.sh/uv/getting-started/installation/
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /usr/local/bin/uv

# ---- Layer 4: non-root user (spec §9.1 step 3 + HF docs "Permissions") --------
# HF Space containers run as UID 1000 by convention; matching that here means
# files written at runtime won't hit EACCES under HF's mount.
# Reference: https://huggingface.co/docs/hub/spaces-sdks-docker#permissions
RUN useradd -m -u 1000 app
USER app
ENV HOME=/home/app \
    PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /home/app

# ---- Layer 5: dependency install (spec §9.1 step 4) ---------------------------
# `uv sync --frozen --no-dev` reproduces the locked transitive resolution from
# uv.lock without resolving fresh. --no-dev drops pytest/ruff (they live under
# [project.optional-dependencies] dev in pyproject.toml).
# `--no-install-project` is implied because we haven't COPY'd the package source
# yet — keeps this layer cacheable across app-code-only changes.
# README.md is COPY'd here because pyproject.toml:5 declares
# `readme = "README.md"`; hatchling (the PEP 517 backend uv invokes during
# `uv sync`) reads that file to build project metadata, so its absence aborts
# the sync with `OSError: Readme file does not exist: README.md`.
COPY --chown=app:app pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- Layer 6: app code (spec §9.1 step 5) -------------------------------------
COPY --chown=app:app app/ ./app/

# Install the project itself (now that source is present). Reuses the locked env.
RUN uv sync --frozen --no-dev

# ---- Layer 7: runtime config (spec §9.1 step 6 + 7) ---------------------------
# Port 7860 = HF Space default. Bind 0.0.0.0 so the HF reverse proxy can reach.
# --workers 1 per spec §3 (single uvicorn worker; concurrency is GlobalLock).
EXPOSE 7860

# HEALTHCHECK per spec §9.1 step 7 + §2.4. Uses curl (installed in Layer 2).
# 5s start-period mirrors DoD #5 ("/api/v1/health returns 200 within 5s").
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:7860/api/v1/health || exit 1

# uv run respects the synced .venv without an explicit activate.
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
