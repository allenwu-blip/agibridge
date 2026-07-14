/**
 * Typed fetch client for the agibridge commercial backend.
 *
 * Generalized from F-1 `frontend/v0-spa:frontend/src/lib/api.ts:20-44`
 * (the `fetchJson` helper) per frontend/D4_rehydration_spec.md §3:
 *   - keep AbortController timeout + FastAPI envelope parsing skeleton
 *   - add `Authorization: Bearer <Clerk JWT>` injection on every call
 *   - `API_BASE = VITE_API_BASE_URL ?? "/api/v1"`; `credentials: "omit"`
 *   - 401 → throw `UnauthenticatedError` (ErrorBoundary → RedirectToSignIn)
 *
 * Endpoint inventory is grounded against the LIVE shipped backend
 * (app/api/jobs.py:166-347), NOT the Cycle H §3.3 draft — the dispatch
 * directs us to the authoritative live contract (dispatches/D4_specs.md §3).
 */

import { useAuth } from "@clerk/clerk-react";
import { useMemo } from "react";
import type {
  ApiErrorDetail,
  ApiErrorEnvelope,
  AuthErrorBody,
  CheckoutRequest,
  CheckoutResponse,
  CreateJobRequest,
  JobListResponse,
  JobView,
  PortalRequest,
  PortalResponse,
  PresignDownloadResponse,
  PresignUploadRequest,
  PresignUploadResponse,
  TierId,
} from "./types";

/**
 * In prod the bundle calls the HF Space directly; in local dev the empty
 * fallback uses the Vite proxy (vite.config.ts). VITE_API_BASE_URL is set
 * verbatim by DevOps in Vercel env per the dispatch env contract.
 */
const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const API_BASE = `${RAW_BASE.replace(/\/$/, "")}/api/v1`;

/** Thrown on 401. Caught by the dashboard ErrorBoundary → RedirectToSignIn. */
export class UnauthenticatedError extends Error {
  constructor(public readonly detail: ApiErrorDetail) {
    super(detail.message);
    this.name = "UnauthenticatedError";
  }
}

/**
 * Thrown on 403 `no_organization` (app/api/clerk_auth.py:202-212): the
 * session is valid but the JWT carries no `org_id` yet. Under auto
 * personal-org (DR-022/DR-023) this is a transient race — the Clerk
 * `user.created` Svix webhook (app/api/webhooks.py) provisions
 * `org_personal_<uid>` and sets `public_metadata.org_id` within ~seconds
 * of sign-up; the next token mint then carries the claim. NOT a sign-out
 * and NOT a "create an organization" prompt (Clerk Orgs is disabled;
 * there is no user-facing org creation).
 */
export class NoOrganizationError extends Error {
  constructor(public readonly detail: ApiErrorDetail) {
    super(detail.message);
    this.name = "NoOrganizationError";
  }
}

/** Generic API failure carrying the parsed envelope + HTTP status. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: ApiErrorDetail,
  ) {
    super(detail.message);
    this.name = "ApiError";
  }
}

type GetToken = () => Promise<string | null>;

/**
 * Parse either error envelope shape:
 *   - route handlers: {detail:{code,message,suggestion}} (app/api/jobs.py:127-131)
 *   - Clerk middleware: flat {code,message,suggestion} (app/api/clerk_auth.py:213-217)
 */
function parseErrorDetail(body: unknown, status: number): ApiErrorDetail {
  if (body && typeof body === "object") {
    const env = body as Partial<ApiErrorEnvelope> & Partial<AuthErrorBody>;
    if (env.detail && typeof env.detail === "object") {
      return env.detail as ApiErrorDetail;
    }
    if (typeof env.code === "string" && typeof env.message === "string") {
      return {
        code: env.code,
        message: env.message,
        suggestion: env.suggestion ?? null,
      };
    }
  }
  return {
    code: `http_${status}`,
    message: `Request failed with status ${status}.`,
    suggestion: null,
  };
}

async function fetchJson<T>(
  getToken: GetToken,
  path: string,
  init: RequestInit,
  timeoutMs = 10_000,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const token = await getToken();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      signal: controller.signal,
      // JWT travels in the header; no cookies. CORS owned by backend
      // (app/main.py allowlist + app/api/clerk_auth.py:193 OPTIONS bypass).
      credentials: "omit",
    });

    if (!response.ok) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      const detail = parseErrorDetail(body, response.status);
      if (response.status === 401) throw new UnauthenticatedError(detail);
      if (response.status === 403 && detail.code === "no_organization") {
        throw new NoOrganizationError(detail);
      }
      throw new ApiError(response.status, detail);
    }

    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The endpoint surface. Each method cites the live backend route.
 * `useApiClient()` closes over Clerk's `getToken` so call sites never touch
 * the Clerk dependency directly (keeps it out of type-only modules).
 */
export interface ApiClient {
  presignUpload(body: PresignUploadRequest): Promise<PresignUploadResponse>;
  createJob(body: CreateJobRequest): Promise<JobView>;
  listJobs(before?: string | null): Promise<JobListResponse>;
  getJob(jobId: string): Promise<JobView>;
  presignDownload(jobId: string): Promise<PresignDownloadResponse>;
  startCheckout(tier: TierId): Promise<CheckoutResponse>;
  openPortal(): Promise<PortalResponse>;
}

function buildClient(getToken: GetToken): ApiClient {
  return {
    // POST /api/v1/jobs/presign-upload — app/api/jobs.py:166-209 (201).
    presignUpload(body) {
      return fetchJson<PresignUploadResponse>(getToken, "/jobs/presign-upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },

    // POST /api/v1/jobs — app/api/jobs.py:212-270 (202). 429 if a conversion
    // is already live (app/api/jobs.py:245-246, single-flight); the thrown
    // ApiError carries that so the caller can back off (Cycle H §3 cadence).
    createJob(body) {
      return fetchJson<JobView>(getToken, "/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },

    // GET /api/v1/jobs — app/api/jobs.py:273-291. Keyset pagination via
    // `before` (the prior page's next_before, app/api/jobs.py:290).
    listJobs(before) {
      const qs = before ? `?before=${encodeURIComponent(before)}` : "";
      return fetchJson<JobListResponse>(
        getToken,
        `/jobs${qs}`,
        { method: "GET" },
        8_000,
      );
    },

    // GET /api/v1/jobs/{id} — app/api/jobs.py:294-311.
    getJob(jobId) {
      return fetchJson<JobView>(
        getToken,
        `/jobs/${encodeURIComponent(jobId)}`,
        { method: "GET" },
        5_000,
      );
    },

    // GET /api/v1/jobs/{id}/presign-download — app/api/jobs.py:314-347.
    // 409 not_ready if state != done; 410 download_expired if output purged.
    presignDownload(jobId) {
      return fetchJson<PresignDownloadResponse>(
        getToken,
        `/jobs/${encodeURIComponent(jobId)}/presign-download`,
        { method: "GET" },
        5_000,
      );
    },

    // POST /api/v1/billing/checkout — merged D4-B contract
    // (app/api/billing.py:64-80): CheckoutRequest requires tier +
    // success_url + cancel_url; frontend supplies its own redirect routes.
    // Call site is the Dashboard go() click handler (client-only, no SSR).
    startCheckout(tier) {
      const body: CheckoutRequest = {
        tier,
        success_url: `${window.location.origin}/dashboard?checkout=success`,
        cancel_url: `${window.location.origin}/dashboard?checkout=cancel`,
      };
      return fetchJson<CheckoutResponse>(getToken, "/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },

    // POST /api/v1/billing/portal — merged D4-B contract
    // (app/api/billing.py:78-80): PortalRequest requires return_url.
    openPortal() {
      const body: PortalRequest = {
        return_url: `${window.location.origin}/dashboard`,
      };
      return fetchJson<PortalResponse>(getToken, "/billing/portal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },
  };
}

/**
 * Hook form per D4_rehydration_spec.md §3.2: closes over Clerk's `getToken`
 * (Clerk v5 returns a short-lived auto-rotated JWT — no manual refresh,
 * https://clerk.com/docs/references/react/use-auth accessed 2026-05-16).
 * Memoized on the stable `getToken` identity so call sites get a steady ref.
 *
 * `template: "agibridge"` selects the custom Clerk JWT template that emits
 * the flat `org_id` claim from `{{user.public_metadata.org_id}}` — the
 * claim the backend middleware reads (`app/api/clerk_auth.py:107-121`).
 * Without the template Clerk returns the default session token (no
 * `org_id`; Organizations is disabled on the instance) and every API call
 * 403s `no_organization`. `public_metadata.org_id` is provisioned by the
 * `user.created` Clerk webhook (`app/api/webhooks.py`).
 *
 * `opts.skipCache` forwards Clerk's `getToken({ skipCache: true })`
 * (`GetTokenOptions`, @clerk/shared types/index.d.ts:2450-2455 — it carries
 * `template?`, `skipCache?`). Clerk caches a minted session token for
 * roughly its lifetime; right after sign-up the cached token predates the
 * webhook that sets `public_metadata.org_id`, so a normal `getToken` keeps
 * re-handing the stale, org-less token and every retry 403s identically.
 * `skipCache` forces a FRESH mint from Clerk, which is what picks up the
 * just-provisioned `org_id` claim — the mechanism the workspace-provisioning
 * retry loop relies on (`useWorkspaceProbe`, `lib/queries.ts`).
 */
export function useApiClient(opts?: { skipCache?: boolean }): ApiClient {
  const { getToken } = useAuth();
  const skipCache = opts?.skipCache ?? false;
  return useMemo(
    () => buildClient(() => getToken({ template: "agibridge", skipCache })),
    [getToken, skipCache],
  );
}
