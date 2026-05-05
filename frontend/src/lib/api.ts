/**
 * Typed fetch wrappers around the agibridge HTTP contract.
 *
 * Every endpoint here cites the backend route file:line that defines it.
 * If you add a new endpoint, cite it; if a contract changes, update both
 * sides and the citation in lib/types.ts.
 */

import type {
  ApiErrorEnvelope,
  Format,
  HealthResponse,
  StatusResponse,
  UploadAccepted,
  UploadResult,
  VersionResponse,
} from "./types";

const API_BASE = "/api/v1";

/** Same-origin fetch with a default timeout. */
async function fetchJson<T>(input: string, init: RequestInit, timeoutMs = 10_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(input, { ...init, signal: controller.signal });
    if (!response.ok) {
      // Try to parse the FastAPI envelope; fall back to status-only error.
      let envelope: ApiErrorEnvelope | null = null;
      try {
        envelope = (await response.json()) as ApiErrorEnvelope;
      } catch {
        envelope = null;
      }
      const detail = envelope?.detail ?? {
        code: `http_${response.status}`,
        message: `Request failed with status ${response.status}.`,
        suggestion: null,
      };
      throw Object.assign(new Error(detail.message), {
        status: response.status,
        detail,
      });
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * POST /api/v1/upload — multipart upload + format selection.
 *
 * Backend route: app/api/upload.py:61-162 (`@router.post("/upload", status_code=202)`).
 * Form fields per app/api/upload.py:62-66:
 *   - file (UploadFile)
 *   - from_format (str)
 *   - to_format (str)
 *   - max_episodes (int | None)
 *
 * Status codes:
 *   - 202 → UploadAccepted (app/api/upload.py:159-170, JSONResponse body)
 *   - 400 invalid_format_pair (lines 73-80)
 *   - 413 archive_too_large (lines 102-110, 132-139)
 *   - 415 unsupported_archive_type | mime_spoofed (lines 117-125, 140-142)
 *   - 422 missing_field (FastAPI's default Form() validation)
 *   - 429 busy with Retry-After: 90 (lines 82-92)
 *   - 500 internal_error (lines 111-113)
 */
export interface UploadParams {
  file: File;
  fromFormat: Format;
  toFormat: Format;
  maxEpisodes: number | null;
}

export async function uploadConversion(params: UploadParams): Promise<UploadResult> {
  const formData = new FormData();
  formData.append("file", params.file);
  formData.append("from_format", params.fromFormat);
  formData.append("to_format", params.toFormat);
  if (params.maxEpisodes !== null) {
    formData.append("max_episodes", String(params.maxEpisodes));
  }

  // No timeout on upload itself — large archives can take 30s+ on residential
  // broadband per spec §2.1 latency note.
  const response = await fetch(`${API_BASE}/upload`, {
    method: "POST",
    body: formData,
  });

  if (response.status === 202) {
    const data = (await response.json()) as UploadAccepted;
    return { kind: "accepted", data };
  }

  // 429 has its own envelope shape per app/api/upload.py:84-92 — top-level
  // {code, message, suggestion} with Retry-After header, NOT the {detail: ...}
  // envelope used by HTTPException routes.
  if (response.status === 429) {
    const retryAfterHeader = response.headers.get("Retry-After");
    const retryAfterSeconds = retryAfterHeader ? parseInt(retryAfterHeader, 10) : 90;
    let payload: { code: string; message: string; suggestion: string | null } = {
      code: "busy",
      message: "Another run is in progress.",
      suggestion: "Please try again in about 90 seconds.",
    };
    try {
      payload = await response.json();
    } catch {
      // keep default
    }
    return {
      kind: "busy",
      retryAfterSeconds: Number.isFinite(retryAfterSeconds) ? retryAfterSeconds : 90,
      detail: payload,
    };
  }

  // All other 4xx/5xx use FastAPI's HTTPException {detail: ...} envelope.
  let envelope: ApiErrorEnvelope | null = null;
  try {
    envelope = (await response.json()) as ApiErrorEnvelope;
  } catch {
    envelope = null;
  }
  return {
    kind: "error",
    status: response.status,
    detail: envelope?.detail ?? {
      code: `http_${response.status}`,
      message: `Upload failed with status ${response.status}.`,
      suggestion: null,
    },
  };
}

/**
 * GET /api/v1/status/{session_id} — poll conversion progress.
 *
 * Backend route: app/api/status.py:22-30 (`@router.get("/status/{session_id}")`).
 * Returns StatusResponse (app/schemas.py:69-87). 404 envelope:
 *   { detail: { code: "session_not_found", message: ..., suggestion: null } }
 *   per app/api/status.py:28-29.
 */
export async function fetchStatus(sessionId: string): Promise<StatusResponse> {
  return fetchJson<StatusResponse>(
    `${API_BASE}/status/${encodeURIComponent(sessionId)}`,
    { method: "GET" },
    /* timeoutMs */ 5_000,
  );
}

/**
 * GET /api/v1/health — used to detect cold-start state.
 *
 * Backend route: app/api/health.py:92-114. Returns HealthResponse on 200, 503
 * `unhealthy` on workspace failure (app/api/health.py:96-105).
 */
export async function fetchHealth(timeoutMs = 5_000): Promise<HealthResponse> {
  return fetchJson<HealthResponse>(
    `${API_BASE}/health`,
    { method: "GET" },
    timeoutMs,
  );
}

/**
 * GET /api/v1/version — F-1 transparency footer.
 *
 * Backend route: app/api/version.py:53-59. Returns VersionResponse with
 * agibridge_git_sha (env GIT_SHA or `git rev-parse HEAD` fallback per
 * app/api/version.py:26-43), embodied_data_version (importlib.metadata),
 * built_at (env BUILT_AT or local-dev tag).
 */
export async function fetchVersion(): Promise<VersionResponse> {
  return fetchJson<VersionResponse>(
    `${API_BASE}/version`,
    { method: "GET" },
    /* timeoutMs */ 5_000,
  );
}

/**
 * Build the same-origin download URL.
 *
 * Backend route: app/api/download.py:32-67. The endpoint streams the result
 * zip with Content-Disposition: attachment; filename="<session_id>.zip"
 * (app/api/download.py:64-66). Initiate via plain anchor click — saves the
 * file to the user's default location.
 */
export function downloadUrlFor(sessionId: string): string {
  return `${API_BASE}/download/${encodeURIComponent(sessionId)}`;
}
