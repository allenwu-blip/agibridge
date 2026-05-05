/**
 * Type mirrors of backend Pydantic models. Source of truth is the committed
 * backend contract — these mirrors must stay aligned with:
 *
 *   - app/schemas.py:14-24   — FromFormat / ToFormat enums (`agibot`, `lerobot-v3`)
 *   - app/schemas.py:25-40   — State enum (pending|extracting|running|validating|done|failed|expired)
 *   - app/schemas.py:41-55   — UploadAccepted (POST /upload 202)
 *   - app/schemas.py:56-70   — ErrorBody (status payload's `error` field on failure)
 *   - app/schemas.py:71-91   — StatusResponse (GET /status/{id})
 *   - app/schemas.py:92-101  — HealthResponse (GET /health)
 *   - app/schemas.py:102-114 — VersionResponse (GET /version)
 *   - app/schemas.py:115-121 — ApiError envelope (HTTPException.detail shape from
 *                              app/api/upload.py:54-58, app/api/download.py:26-29)
 *
 * If backend's contract diverges from this file, we surface a DR rather than
 * silently re-shape the client.
 */

export type Format = "agibot" | "lerobot-v3";

export const FORMATS = ["agibot", "lerobot-v3"] as const;

/**
 * Spec §2.1 / convert/__init__.py:19-22 _SUPPORTED — the only valid pairs.
 * Mirrored from app/api/upload.py:39 (_SUPPORTED_PAIRS).
 */
export const SUPPORTED_PAIRS: ReadonlyArray<[Format, Format]> = [
  ["agibot", "lerobot-v3"],
  ["lerobot-v3", "agibot"],
];

/** State machine. Mirrors app/schemas.py:25-40. */
export type ConversionState =
  | "pending"
  | "extracting"
  | "running"
  | "validating"
  | "done"
  | "failed"
  | "expired";

/** ErrorBody. Mirrors app/schemas.py:56-70. */
export interface ConversionError {
  /**
   * Wrapper-side enum. Spec §5 mapping table — NOT from the lib (`_emit.py:41`
   * has no `error.code` field). Examples we render specially via voice-guide
   * Surface 2: `converter_rejected_input`, `oom_suspected`, `conversion_timeout`.
   */
  code: string;
  message: string;
  suggestion: string | null;
  stderr_tail: string | null;
}

/** StatusResponse. Mirrors app/schemas.py:71-91. */
export interface StatusResponse {
  session_id: string;
  state: ConversionState;
  started_at: string | null;
  finished_at: string | null;
  expires_at: string;
  error: ConversionError | null;
  download_url: string | null;
  /**
   * §2.2.1 wrapper-side estimate. int 0..99 while state in {running,validating},
   * else null. NEVER 100 before state == done — backend clamps at 99 in
   * app/api/status.py:77-78 (clamp at 99); UI must surface the "(estimate)" annotation
   * verbatim per spec §2.2.1 honesty constraint #1.
   */
  estimated_progress_pct: number | null;
}

/** UploadAccepted. Mirrors app/schemas.py:41-55 / app/api/upload.py:159-170 (JSONResponse 202 body). */
export interface UploadAccepted {
  session_id: string;
  status_url: string;
  expires_at: string;
  note: string;
}

/** HealthResponse. Mirrors app/schemas.py:92-101. */
export interface HealthResponse {
  ok: boolean;
  embodied_data_version: string;
  uptime_s: number;
  active_session: string | null;
  free_disk_mb: number;
}

/** VersionResponse. Mirrors app/schemas.py:102-114 / app/api/version.py:53-66 (route handler). */
export interface VersionResponse {
  agibridge_git_sha: string;
  embodied_data_version: string;
  built_at: string;
}

/**
 * ApiError envelope returned via FastAPI's `detail`. Wrapped in `{detail: ...}`
 * by FastAPI when the route raises HTTPException — see
 * app/api/upload.py:54-58 (_err helper) and app/api/download.py:26-29.
 */
export interface ApiErrorDetail {
  code: string;
  message: string;
  suggestion: string | null;
}

export interface ApiErrorEnvelope {
  detail: ApiErrorDetail;
}

/** Discriminated union for the upload result — keeps call sites exhaustive. */
export type UploadResult =
  | { kind: "accepted"; data: UploadAccepted }
  | { kind: "busy"; retryAfterSeconds: number; detail: ApiErrorDetail }
  | { kind: "error"; status: number; detail: ApiErrorDetail };

/** Possible terminal-or-active phases of the upload flow. */
export type UploadFlowPhase =
  | { kind: "idle" }
  | { kind: "uploading" }
  | { kind: "polling"; sessionId: string; lastStatus: StatusResponse }
  | { kind: "done"; sessionId: string; downloadUrl: string }
  | { kind: "failed"; sessionId: string; error: ConversionError }
  | { kind: "rejected"; status: number; detail: ApiErrorDetail };
