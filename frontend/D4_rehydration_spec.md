# D4 frontend-dev rehydration spec — Cycle H

**Owner**: frontend-dev (Cycle H spec author)
**Consumer**: D4 frontend-dev (implementer)
**Date**: 2026-05-14 (W1 D3)
**Inputs read**: `/Users/allenwu/.plans/agibridge-2026/CLAUDE.md`, `DECISIONS.md` (DR-005 Clerk, DR-011 reuse correction), `_w1_deliverables/frontend-dev-D1-D3.md`, `_day1_research/tech_spec.md` story #9, git blob `880aab8` on branch `frontend/v0-spa`.
**Scope**: SPEC ONLY. No code emitted. D4 frontend-dev implements against this contract.

---

## 1. Pre-flight git archeology

Verified via `git ls-tree -r --long frontend/v0-spa -- frontend/` and `git show frontend/v0-spa:<path>` from `/Users/allenwu/embodied-data-hosted/agibridge`. Branch head `880aab8`. All entries below exist; none invented. Verdict + line counts consolidated into §6 — this section confirms file presence + byte sizes only.

| Git ref | bytes |
|---|---:|
| `frontend/v0-spa:frontend/package.json` | 929 |
| `frontend/v0-spa:frontend/index.html` | 884 |
| `frontend/v0-spa:frontend/vite.config.ts` | 1007 |
| `frontend/v0-spa:frontend/tsconfig.json` | 646 |
| `frontend/v0-spa:frontend/tailwind.config.js` | 574 |
| `frontend/v0-spa:frontend/src/main.tsx` | 334 |
| `frontend/v0-spa:frontend/src/App.tsx` | 7723 |
| `frontend/v0-spa:frontend/src/components/UploadForm.tsx` | 8041 |
| `frontend/v0-spa:frontend/src/components/{BetaBanner,ColdStartOverlay,EmptyState,ErrorToast,Footer,ProgressBar,StatusPanel}.tsx` | 2291 / 1058 / 2594 / 5689 / 1342 / 3144 / 2384 |
| `frontend/v0-spa:frontend/src/lib/{api,copy,types,useColdStart,usePoller,useVersion}.ts` | 6292 / 8139 / 4350 / 2095 / 2787 / 959 |
| `frontend/v0-spa:frontend/scripts/{check-f1,integration-probe}.mjs` | 6434 / 5674 |
| `frontend/v0-spa:frontend/src/test/*.test.tsx` (6 files) | ~16 KB total |

Lines for files consumed in §2–§5 (via `git show <ref> | wc -l`): `UploadForm.tsx` 220, `api.ts` 195, `types.ts` 125, `usePoller.ts` 92, `App.tsx` 240, `main.tsx` 13, `vite.config.ts` 37, `package.json` 31.

---

## 2. `UploadForm.tsx:38-100` line-by-line refactor plan

Source: `frontend/v0-spa:frontend/src/components/UploadForm.tsx:38-100`. The line range covers the component declaration through `onSubmitForm` handler — the exact span where contract shifts from "single-session SPA convert" to "org-scoped multi-job presigned upload".

### 2.1 Current state (line ranges cite `frontend/v0-spa:frontend/src/components/UploadForm.tsx`)

- `:38-43` — signature `{disabled, initialFromFormat, initialToFormat, onSubmit: (s: UploadSubmission) => void}`. `UploadSubmission` at `:18-23` = `{file, fromFormat, toFormat, maxEpisodes}`.
- `:44-51` — state: `formId` (useId), `fileInputRef`, `file`, `fromFormat`, `toFormat`, `maxEpisodes`, `dragActive`, `validationError`.
- `:53-69` — `handleFiles`; extension allowlist at `:60-64` (`.zip`, `.tar.gz`, `.tgz`) cites backend magic-byte check `app/api/upload.py:34-35 + 202-210`.
- `:71-78` — `onDrop` (`preventDefault` + clear dragActive + `handleFiles(e.dataTransfer.files)`).
- `:80-100` — `onSubmitForm`: file-present check, `isPairSupported(fromFormat, toFormat)` (`:33-36` against `SUPPORTED_PAIRS` from `frontend/v0-spa:frontend/src/lib/types.ts:27-30`), parse optional `maxEpisodes`, fire `onSubmit`.

### 2.2 Target state

Org-scoped multi-job upload with R2 presigned PUT. Browser-direct (bytes never traverse the API origin); backend issues short-lived PUT URL, then receives a "start" call once the R2 object exists. Flow per D1-D3 §3.3 + backend-dev D4:
1. `POST /api/v1/jobs/presign-upload` `{filename, size_bytes}` → `{job_id, upload_url, expires_at}`.
2. `PUT` to `upload_url` via XHR (fetch upload-progress not yet shipped in Chrome stable as of 2026-05).
3. `POST /api/v1/jobs/{job_id}/start` on R2 200 → backend transitions `presigned` → `queued`.
4. Optimistic row prepend on `JobsTable` (parent owns list; form fires callback only).
5. Reconcile via TanStack Query `invalidateQueries(['jobs'])`.

### 2.3 Verbatim-keep (high-confidence salvage)

- `:53-69` `handleFiles` + extension allowlist — keep entire body; add bare `.tar` to the allowlist at `:62-63` per D1-D3 §3.3 (AgiBot World ships uncompressed tar).
- `:71-78` `onDrop` — verbatim.
- JSX drag-drop label region (~`:110-138` in the file, including Tailwind dragActive classes) — verbatim.
- `useId` + `fileInputRef` at `:44-45` — verbatim.
- `validationError` state + `<p role="alert">` render — verbatim (a11y semantics already correct).

### 2.4 Refactor (before → after)

- **Props (`:38-43`)**: replace `{disabled, initialFromFormat, initialToFormat, onSubmit}` with `{disabled, onPresignAndStart}`. `onPresignAndStart: (p: {file: File}) => Promise<void>` is supplied by parent `DashboardHome` and internally runs presign → PUT → start. Form stays presentational; XHR-progress lives in a `useUpload(file)` hook owned by parent.
- **State (`:44-51`)**: drop `fromFormat`, `toFormat`, `maxEpisodes`. Keep `file`, `dragActive`, `validationError`. Add `uploadPct: number | null` (driven by XHR `upload.onprogress`).
- **Validation (`:80-100`)**: collapse three checks (file present + `isPairSupported` + maxEpisodes int) to one — file present. Single-direction MVP per D1-D3 §1.2 retires `isPairSupported`; backend-dev D1-D3 confirms `/api/v1/jobs` has no `max_episodes` field. ~18 lines → ~5.
- **JSX delete**: the two `<select>` blocks for from/to format and the `max_episodes` `<input>` block (the three field-groups that occupy the JSX body below the label-card).
- **JSX add**: a `<progress value={uploadPct} max={100} aria-label="Upload progress">` region inside the label-card, visible only when `uploadPct !== null`.

### 2.5 Callback contract change

Before: `onSubmit({file, fromFormat, toFormat, maxEpisodes})` fired synchronously. After: `await onPresignAndStart({file})`; parent owns the 3-step orchestration + optimistic insertion. Parent surfaces upload errors via `uploadError?: ApiErrorDetail` prop, which the form renders through the existing `validationError` slot — error-rendering JSX unchanged.

---

## 3. `lib/api.ts:18-44` generalization

Source: `frontend/v0-spa:frontend/src/lib/api.ts:18-44`. The line range is the `fetchJson` helper — same-origin fetch with `AbortController` timeout and `ApiErrorEnvelope` parsing.

### 3.1 Current state

- `:18` `const API_BASE = "/api/v1"`.
- `:20-44` `fetchJson<T>(input, init, timeoutMs=10_000)`: `AbortController` timeout; parses FastAPI `{detail: {code, message, suggestion}}` on non-OK; throws `Error` with `status` + `detail`. No auth header.

### 3.2 Target state

Same envelope parsing + same `AbortController`; add:
- **Bearer injection** on every call from Clerk `useAuth().getToken()` (Clerk v5 returns a short-lived JWT, auto-rotated, no manual refresh).
- **Hook form**: `useApiClient()` closes over `getToken`, returns the same `fetchJson<T>` surface. Keeps Clerk dep out of type-only imports.
- **401**: throw `UnauthenticatedError`; top-level `<ErrorBoundary>` redirects to `/sign-in` (rare but possible on long-idle tabs).
- **Origin**: `API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1"`; `credentials: "omit"` (JWT in header, no cookies). CORS owned by backend-dev.

### 3.3 Endpoint inventory for MVP

Verified against `backend-dev-D1-D3.md` + planned D4 routes. Every endpoint requires `Authorization: Bearer <jwt>`; `org_id` derives from JWT claim server-side (never client-trusted per `tech_spec.md:221`).

**`POST /api/v1/jobs/presign-upload`**
```ts
interface PresignUploadRequest {
  filename: string;       // original filename incl. extension
  size_bytes: number;     // pre-flight cap check server-side
}
interface PresignUploadResponse {
  job_id: string;         // ULID/UUID; backend already inserted job row in 'presigned' state
  upload_url: string;     // R2 presigned PUT URL, ~10 min expiry
  upload_headers: Record<string, string>;  // headers R2 requires on the PUT (Content-Type, etc.)
  expires_at: string;     // ISO 8601, when upload_url stops working
}
```

**`POST /api/v1/jobs/{job_id}/start`** (called after R2 PUT returns 200)
```ts
// Request body empty. job_id in path.
interface JobStartResponse {
  job: Job;               // job row with status="queued"
}
```

**`GET /api/v1/jobs`**
```ts
interface ListJobsResponse {
  jobs: Job[];            // newest first, org-scoped server-side from JWT
  next_cursor: string | null;  // pagination if >50 jobs
}
```

**`GET /api/v1/jobs/{job_id}`**
```ts
// Response is Job directly.
```

**`GET /api/v1/jobs/{job_id}/presign-download`**
```ts
interface PresignDownloadResponse {
  download_url: string;   // R2 presigned GET, ~10 min expiry
  expires_at: string;
  filename: string;       // suggested filename for the saved file
}
```

### 3.4 Shared `Job` interface

```ts
type JobStatus = "presigned" | "queued" | "running" | "done" | "failed" | "expired";
interface Job {
  id: string; filename: string; size_bytes: number; status: JobStatus;
  created_at: string; started_at: string | null; finished_at: string | null;
  error: ApiErrorDetail | null;
}
```

`download_url` deliberately NOT inlined — frontend calls `/presign-download` on click for a fresh URL (avoids stale-URL UX). `ApiErrorDetail` + `ApiErrorEnvelope` reused verbatim from `frontend/v0-spa:frontend/src/lib/types.ts:100-108`; FastAPI `HTTPException` shape unchanged across F-1 and SaaS.

---

## 4. TanStack Query integration spec for live job polling

### 4.1 Choice: TanStack Query v5 (recommend over SWR)

Reasoning:
- **Mutations + invalidation**: `useMutation` + `invalidateQueries(['jobs'])` cleanly separates cache write from revalidation for the presign → PUT → start chain; SWR's `mutate(key)` conflates both.
- **Dynamic polling**: `refetchInterval: (query) => number | false` is documented for stop-on-terminal (`https://tanstack.com/query/latest/docs/framework/react/guides/polling`, accessed 2026-05-14); SWR's `refreshInterval` function form is less documented for this case.
- **Devtools**: `@tanstack/react-query-devtools` shows live query state, cache hits, retry counts.
- **Project fit**: query-key cache aligns with org-scoped reads (`['jobs', orgId]`).

### 4.2 Polling interval strategy

**`useJob(jobId)`** — `queryKey: ['job', jobId]`, `staleTime: 1_000`, `refetchInterval: (query) => { const s = query.state.data?.status; if (!s) return 5000; if (s === 'done' || s === 'failed' || s === 'expired') return false; if (s === 'running') return 2000; return 5000; }`. Returning `false` halts the timer per TanStack docs (see citation §4.1).

**`useJobs()`** — `queryKey: ['jobs']`, `refetchOnWindowFocus: true`, `refetchInterval: (query) => query.state.data?.jobs.some(j => j.status === 'queued' || j.status === 'running') ? 3000 : 30000`.

Per TanStack defaults (`https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults`, accessed 2026-05-14): queries stale-immediately; failed queries auto-retry 3× exponential backoff. Acceptable here. `staleTime: 1_000` on `useJob` prevents re-render storms during rapid state transitions.

**Backend protection**: with ≤10 jobs/org typical and 3 s active interval, peak ≈20 req/min/org. Backend-dev rate-limits 60 req/min/org with 429; TanStack's built-in backoff handles 429 without custom code.

### 4.3 Optimistic update pattern

`useMutation` for `startJob`: `onMutate` cancels in-flight `['jobs']` queries, snapshots previous data, prepends optimistic row `{id: jobId, status: 'queued', ...placeholders}` to the cached list; `onError` rolls back from snapshot; `onSettled` calls `invalidateQueries(['jobs'])` to reconcile to server truth.

Do NOT optimistically render the presign step — the job has no real bytes yet. Show upload progress inside `UploadForm`; only insert into `JobsTable` after `/start` returns.

### 4.4 Cache invalidation on user actions

- Delete job (W2; not D4): `useMutation` with `onSuccess: () => queryClient.invalidateQueries({queryKey: ['jobs']})`.
- Retry job (W2; not D4): same invalidation pattern.
- Sign-out: Clerk `<UserButton>` `afterSignOutUrl` triggers a route change; on `/sign-in` mount, dispatch `queryClient.clear()` — prevents next signed-in user from seeing previous tenant's cached jobs.

### 4.5 Provider wiring (parent of D4 work)

`<QueryClientProvider client={queryClient}>` wraps `<App>` inside `<ClerkProvider>` in `main.tsx`. Single `QueryClient` instance per module; not per render. Devtools mount only in dev: `{import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}`.

---

## 5. Loading / error / empty states for dashboard

### 5.1 Empty state — no jobs yet

Component `<EmptyJobs />` (supersedes `frontend/v0-spa:frontend/src/components/EmptyState.tsx`). Trigger: `useJobs()` `status === 'success'` + `jobs.length === 0`.

Copy: heading "No conversions yet"; body "Upload your first dataset above to convert it. Conversions appear here as they run." No CTA button — upload card sits above; duplicate CTA splits attention.

Inheritance from `EmptyState.tsx`: outer `<section aria-labelledby>` shell + Tailwind classes only. Copy + F-1 sample-dataset list (`SAMPLE_DATASETS` from `frontend/v0-spa:frontend/src/lib/copy.ts`) deleted entirely.

### 5.2 Loading state — initial fetch

On `useJobs()` `status === 'pending'`: render `<JobsTable.Skeleton rows={3} />` — three `<tr>` each with four `<td>` containing pulsing `bg-stone-200 dark:bg-stone-800` spans. Same `<table>` chrome so layout doesn't jump. Skeleton over spinner because content-shaped placeholders reduce perceived LCP delay; JobsTable row shape is predictable.

### 5.3 Error states

- **Backend 5xx**: `useQuery` resolves with `error`; render `<section role="alert">` with heading "Couldn't load your conversions.", body "The server returned an error. Retry below; if it keeps failing, check status.agibridge.dev.", and a Retry button calling `refetch()`.
- **Network down** (`error.message === 'Failed to fetch'`): same shell, copy "Looks like you're offline. Reconnect and retry."
- **Auth expired (401)**: `useApiClient.fetchJson` throws `class UnauthenticatedError extends Error`; `<ErrorBoundary>` wrapping `<DashboardLayout>` catches via `getDerivedStateFromError` (class match) and renders Clerk's `<RedirectToSignIn />`. No broken-chrome flash.

### 5.4 Per-job state surfaces in `JobsTable`

Status badge column — color decorative, `aria-label` carries semantics: `presigned` gray dot "upload pending"; `queued` amber dot; `running` blue spinner; `done` green check; `failed` red X "click row for details"; `expired` gray dash "file purged".

Action column: `done` → Download button (calls `/presign-download` then triggers anchor `download` click); `failed` → View-error button (discloses `error.message` + `error.suggestion` + `error.code` chip); else → em-dash.

### 5.5 F-1 reuse audit (per-component verdict — extends D1-D3 §1.2; full table in §6)

- `BetaBanner` — no reuse. F-1 spec §5 AgiBot caveat (per `frontend/v0-spa:frontend/src/components/BetaBanner.tsx:1-15` comment citing "Phase D A2.4") has no SaaS equivalent.
- `EmptyState` — shell salvage only; copy + `SAMPLE_DATASETS` list deleted.
- `StatusPanel` — no reuse. Renders one StatusResponse; dashboard needs array view. Rewrite as `<JobRow>` + `<JobsTable>`.
- `ErrorToast` — partial. Keep `Code` helper + `<div role="alert" aria-live="assertive">` shell; drop voice-guide switch at `frontend/v0-spa:frontend/src/components/ErrorToast.tsx:106-149`. SaaS error catalog is backend-dev's surface; may diverge from F-1 spec §5.
- `ProgressBar` — defer to W2 job-detail view; `aria-valuemin/valuemax/valuenow` semantics reusable verbatim.

---

## 6. Delete vs salvage detailed table

Extends D1-D3 §1.2 with line counts + verdict per file.

| File (`frontend/v0-spa:frontend/...`) | lines | Verdict | Notes |
|---|---:|---|---|
| `package.json` | 31 | refactor | +`@clerk/clerk-react@^5.x`, +`react-router-dom@^6.28`, +`@tanstack/react-query@^5.x` (+ devtools dev-dep); drop `lint:f1` |
| `index.html` | — | refactor | rewrite `<title>` + meta description (F-1 "Hobby project. No accounts." must go) |
| `vite.config.ts` | 37 | refactor | drop `outDir: ../app/static`; keep `react()` plugin, vitest block, dev proxy (re-target to backend on `:8000` per backend-dev D1-D3) |
| `tsconfig.json` | — | keep verbatim | strict + `noUnusedLocals` already correct |
| `tailwind.config.js` | — | keep verbatim | content globs cover `src/**/*.{ts,tsx}` |
| `src/main.tsx` | 13 | refactor | wrap `<ClerkProvider>` + `<QueryClientProvider>` + `<BrowserRouter>` |
| `src/App.tsx` | 240 | delete + rewrite | router shell per D1-D3 §5 |
| `src/components/UploadForm.tsx` | 220 | partial salvage | per §2 — keep `:53-78` drag-drop + JSX label region; drop format/maxEpisodes |
| `src/components/BetaBanner.tsx` | — | delete | F-1 spec §5 banner irrelevant |
| `src/components/ColdStartOverlay.tsx` | — | delete | HF Space cold-start UX gone |
| `src/components/EmptyState.tsx` | — | partial salvage | shell only; copy + sample-dataset list deleted |
| `src/components/ErrorToast.tsx` | — | partial salvage | `Code` helper + outer shell only; voice-guide branches at `:106-149` deleted |
| `src/components/Footer.tsx` | — | delete | F-1 transparency footer (`/api/v1/version`) not in SaaS surface |
| `src/components/ProgressBar.tsx` | — | defer (W2) | reuse on per-job detail page; not D4 critical |
| `src/components/StatusPanel.tsx` | — | delete | single-StatusResponse shape doesn't fit list of jobs |
| `src/lib/api.ts` | 195 | refactor | per §3 — keep `:20-44` envelope skeleton, add Bearer injection, new endpoint inventory |
| `src/lib/copy.ts` | — | delete | F-1 voice-guide copy stale per DR-011 |
| `src/lib/types.ts` | 125 | refactor | keep `ApiErrorDetail` + `ApiErrorEnvelope` at `:100-108`; drop `Format`, `SUPPORTED_PAIRS`, `ConversionState`, `UploadFlowPhase`; add `Job`, `JobStatus`, `OrgContext`, §3.3 req/resp types |
| `src/lib/useColdStart.ts` | — | delete | HF Space cold-start probe — irrelevant on Cloudflare Pages / Vercel |
| `src/lib/usePoller.ts` | 92 | delete | replaced by TanStack Query — see §4 |
| `src/lib/useVersion.ts` | — | delete | F-1 footer gone |
| `scripts/check-f1.mjs` | — | delete | F-1 banned-words gate retired |
| `scripts/integration-probe.mjs` | — | delete | F-1 probe; new probe owned by D4 or DevOps |
| `src/test/*.test.tsx` (6 files) | — | delete + rewrite | new suite per §7 |

---

## 7. Acceptance criteria for D4 frontend-dev implementation

1. **Build**: `bun install` + `bun run build` succeed; `tsc -b --noEmit` clean; `bun run dev` on `:5173` no console errors.
2. **Providers**: `<ClerkProvider>` → `<QueryClientProvider>` → `<BrowserRouter>` → `<App />` in `src/main.tsx`; Devtools mount only in DEV.
3. **`useApiClient` hook**: injects `Authorization: Bearer <token>` from `useAuth().getToken()`; 401 throws `UnauthenticatedError`; preserves `AbortController` + envelope parsing from `frontend/v0-spa:frontend/src/lib/api.ts:20-44`.
4. **Endpoint client**: 5 endpoints from §3.3 implemented with typed interfaces; all call sites use `useApiClient()`.
5. **`UploadForm` refactor**: drag-drop verbatim from `frontend/v0-spa:frontend/src/components/UploadForm.tsx:53-78`; format/maxEpisodes UI removed; callback is `onPresignAndStart`; upload-progress visible.
6. **`useUpload(file)` hook**: presign → XHR PUT (with `upload.onprogress` → `setUploadPct`) → start. XHR not fetch.
7. **`useJobs()` + `useJob(id)`**: TanStack Query with §4.2 `refetchInterval` functions; stop on terminal states.
8. **JobsTable**: skeleton (3 rows) on pending; empty state per §5.1 on success+empty; rendered table on data.
9. **Error UX**: 5xx and network → `<section role="alert">` + Retry button; 401 → `<ErrorBoundary>` → `<RedirectToSignIn>`.
10. **Optimistic update**: new row appears within 1 frame of `/start` return; reconciles within 3 s.
11. **A11y**: Lighthouse a11y ≥ 95 on `/dashboard`; keyboard-only Tab flow verified; status badge color decorative + `aria-label` semantic.
12. **Tests**: vitest for `useApiClient` (mock `getToken`, assert header), `UploadForm` drag-drop validation, `JobsTable` empty/loading/data/error branches. No F-1 voice-guide tests.
13. **TS discipline**: no `any`, no `@ts-ignore` without comment; discriminated unions for `JobStatus` and Query states.
14. **Bundle**: gzipped initial chunk ≤ 350 KB (sanity floor — Clerk + TanStack + React + Router).

---

## 8. Open questions / [DECISION NEEDED]

- **None for D4 implementation**. All decisions either inherited from DR-005 / DR-011 / D1-D3 deliverable or resolved within this spec.
- **W2 follow-ups** (out of D4 scope, noted for cross-brief propagation): job-detail route `/dashboard/jobs/:id` reuses `ProgressBar.tsx`; W2 may reintroduce format-pair selector when second direction (LeRobot v3 → AgiBot) goes commercial.
