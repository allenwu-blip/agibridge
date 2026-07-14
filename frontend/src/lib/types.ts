/**
 * Type mirrors of the LIVE backend contract. Source of truth is the deployed
 * D4-A backend (merged to main, running on the HF Space). Every interface
 * cites the backend `file:line` that defines it.
 *
 * Authoritative sources (this repo, branch feat/d4f-frontend off origin/main):
 *   - app/api/jobs.py:76-119   — Presign/Create/JobView/List/Download models
 *   - app/schemas.py:25-38     — State enum
 *   - app/api/clerk_auth.py:213-217 — flat {code,message,suggestion} 401/403 envelope
 *   - app/api/jobs.py:127-131  — {detail:{code,message,suggestion}} route envelope
 *
 * NOTE: the Cycle H rehydration spec (frontend/D4_rehydration_spec.md §3.3)
 * drafted `POST /jobs/{id}/start` + a `Job` shape with `filename`/`size_bytes`.
 * The dispatch directs us to follow the AUTHORITATIVE LIVE contract in
 * dispatches/D4_specs.md §3 instead — which the shipped backend implements as
 * `POST /api/v1/jobs` with the JobView shape below. We mirror the live code.
 */

/** Conversion direction. Backend `_SUPPORTED_PAIRS` in app/api/jobs.py:181. */
export type Format = "agibot" | "lerobot-v3";

export const FORMATS = ["agibot", "lerobot-v3"] as const;

/**
 * The only valid pairs. Mirrors app/api/jobs.py:180-187 (`pair not in
 * _SUPPORTED_PAIRS` → 400 invalid_format_pair).
 */
export const SUPPORTED_PAIRS: ReadonlyArray<[Format, Format]> = [
  ["agibot", "lerobot-v3"],
  ["lerobot-v3", "agibot"],
];

/**
 * Job lifecycle state machine. Mirrors app/schemas.py:32-38 (`State` enum) /
 * app/db/models.py:87-93 (`JobState` enum). `validating` reserved per
 * app/schemas.py:28-29 (v0 collapses convert+validate into `running`).
 */
export type JobState =
  | "pending"
  | "extracting"
  | "running"
  | "validating"
  | "done"
  | "failed"
  | "expired";

/** States after which polling must stop (no further transitions). */
export const TERMINAL_STATES: ReadonlySet<JobState> = new Set<JobState>([
  "done",
  "failed",
  "expired",
]);

/** Active states (conversion in flight) — drive faster poll cadence. */
export const ACTIVE_STATES: ReadonlySet<JobState> = new Set<JobState>([
  "extracting",
  "running",
  "validating",
]);

/**
 * POST /api/v1/jobs/presign-upload request.
 * Mirrors app/api/jobs.py `PresignUploadRequest`. No `org_id` — it derives
 * from the JWT server-side (R3). `size_bytes` (Track 4 edge-hardening) is
 * the archive's byte length; the backend uses it to reject an oversized
 * upload with a 413 `upload_too_large` before issuing the R2 URL. Optional
 * on the wire (`int | None`, `ge=1`); the frontend always sends `File.size`.
 */
export interface PresignUploadRequest {
  filename: string;
  from_format: Format;
  to_format: Format;
  size_bytes?: number;
}

/**
 * POST /api/v1/jobs/presign-upload 201 response.
 * Mirrors app/api/jobs.py:85-89 + the construction at app/api/jobs.py:204-209.
 */
export interface PresignUploadResponse {
  job_id: string;
  upload_url: string;
  key: string;
  expires_in: number;
}

/**
 * POST /api/v1/jobs request (called AFTER the R2 PUT 200).
 * Mirrors app/api/jobs.py:92-97 (`CreateJobRequest`).
 */
export interface CreateJobRequest {
  job_id: string;
  max_episodes?: number | null;
}

/**
 * Single job view. Mirrors app/api/jobs.py:100-110 (`JobView`) — the response
 * of POST /api/v1/jobs (202), GET /api/v1/jobs/{id}, and each element of the
 * list response. Timestamps are ISO-8601 strings over the wire (FastAPI
 * serializes `datetime`).
 */
export interface JobView {
  job_id: string;
  state: JobState;
  from_format: string | null;
  to_format: string | null;
  error_code: string | null;
  error_msg: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/**
 * GET /api/v1/jobs response. Mirrors app/api/jobs.py:112-114
 * (`JobListResponse`). `next_before` is an ISO timestamp keyset cursor
 * (app/api/jobs.py:290) — pass it back as the `before` query param.
 */
export interface JobListResponse {
  jobs: JobView[];
  next_before: string | null;
}

/**
 * GET /api/v1/jobs/{id}/presign-download response.
 * Mirrors app/api/jobs.py:117-119 (`PresignDownloadResponse`).
 */
export interface PresignDownloadResponse {
  download_url: string;
  expires_in: number;
}

/**
 * Route-level error envelope. FastAPI wraps `HTTPException.detail` so the
 * shape is `{detail:{code,message,suggestion}}` per app/api/jobs.py:127-131
 * (`_err`). Reused verbatim from F-1
 * (frontend/v0-spa:frontend/src/lib/types.ts:100-108) — shape unchanged.
 */
export interface ApiErrorDetail {
  code: string;
  message: string;
  suggestion: string | null;
}

export interface ApiErrorEnvelope {
  detail: ApiErrorDetail;
}

/**
 * Middleware-level error envelope. The Clerk middleware short-circuits with a
 * FLAT body (NOT wrapped in `detail`) per app/api/clerk_auth.py:213-217:
 * `{code, message, suggestion: null}`. 401 = unknown identity; 403 with
 * `code === "no_organization"` = valid session but no Clerk org
 * (app/api/clerk_auth.py:202-212).
 */
export interface AuthErrorBody {
  code: string;
  message: string;
  suggestion: string | null;
}

/**
 * Billing endpoints. Request shapes mirror the merged D4-B contract verbatim
 * (app/api/billing.py:64-80): success_url/cancel_url/return_url are required —
 * the frontend supplies its own post-redirect routes (correct Stripe practice).
 */
export interface CheckoutRequest {
  tier: TierId;
  success_url: string;
  cancel_url: string;
}
export interface PortalRequest {
  return_url: string;
}
export interface CheckoutResponse {
  url: string;
}
export interface PortalResponse {
  url: string;
}

/** Pricing tier identity used by the marketing landing + upgrade CTA. */
export type TierId = "free" | "solo" | "team" | "enterprise";

/**
 * One 5-check validator row. Mirrors the `checks[]` element the demo
 * endpoint builds from the embodied-data `--verify` envelope
 * (app/api/demo.py — `validate/_report.py:53-72`: name/status/detail,
 * status ∈ PASS|WARN|FAIL|SKIP).
 */
export interface DemoCheck {
  name: string | null;
  status: string | null;
  detail: string | null;
}

/**
 * `POST /api/v1/demo/convert` response (always HTTP 200 unless 429/503).
 * Mirrors app/api/demo.py: on success `ok:true` + the REAL validator
 * result + the REAL converted dataset shape read off disk; on any
 * internal failure `ok:false` with a soft message (the demo NEVER 500s
 * onto Landing). `state` is "done" | "busy" | "error".
 */
export interface DemoResult {
  ok: boolean;
  state: "done" | "busy" | "error";
  from_format?: string;
  to_format?: string;
  sample?: string;
  validator_result?: string | null;
  checks?: DemoCheck[];
  dataset?: {
    codebase_version: string | null;
    episodes: number | null;
    frames: number | null;
    tasks: number | null;
    fps: number | null;
    output_bytes: number;
    file_tree: string[];
  };
  message?: string;
  suggestion?: string;
}
