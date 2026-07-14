# agibridge TypeScript SDK — design spec

**Status**: DRAFT (contract). Implementation deferred to D5+ feature wave.
**Author**: Cycle AA Wave 3 (Developer Advocate, orchestrator-direct)
**Date**: 2026-05-14
**Audience**: Next.js / Vite frontend developers integrating agibridge into customer dashboards; Node.js server-side scripts; Deno / Bun edge runtimes.
**Voice anchor**: terse, source-cited, no marketing exclamation — inherits `marketing/site-copy/homepage.md` Cycle X profile.

This spec defines the contract for the `agibridge` TypeScript SDK. Same constraint as `python-spec.md`: backend D4 (`dispatches/D4_specs.md` §4) does NOT implement API keys; D4 ships Clerk JWT only. The API key issuance backend lands D5+. SDK ships against the contract.

---

## 1. Package name + availability

**Recommended npm name**: `agibridge`.

**Availability verified 2026-05-14**:
- `https://registry.npmjs.org/agibridge` → HTTP 404 (available)
- `https://registry.npmjs.org/agibridge-sdk` → HTTP 404 (available — fallback if `agibridge` lost)
- `https://registry.npmjs.org/@agibridge/sdk` → HTTP 404 (available — scoped fallback)

**Recommended action**: reserve `agibridge` AND the `@agibridge` org scope on npm before D5. Reserving the org scope blocks typosquatting (`@agibridge/types`, `@agibridge/cli` etc. for future packages).

**Scoped vs unscoped**: ship as unscoped `agibridge` to match the PyPI distribution name (`python-spec.md` §1). Cross-language SDK discoverability is higher when names match.

---

## 2. Targets

| Target | Module format | Auth source | Why |
|---|---|---|---|
| Browser (Vite, Next.js, Remix) | ESM | Clerk JWT via `useAuth().getToken()` | Bundler-friendly tree-shake, Clerk session already present |
| Node.js ≥20 LTS | ESM + CJS dual | `process.env.AGIBRIDGE_API_KEY` | LTS at time of spec; ESM-native since 20 (`https://nodejs.org/en/about/previous-releases`, accessed 2026-05-14) |
| Deno / Bun / Cloudflare Workers | ESM | env-binding (`Deno.env.get`, `Bun.env`, Worker `env.AGIBRIDGE_API_KEY`) | Same Web `fetch` API, no shim |

**Compatibility matrix**:
- Node.js: ≥20 LTS (drops Node 18 EOL April 2025 per `https://nodejs.org/en/about/previous-releases`, accessed 2026-05-14). Node 22 active LTS as of 2026-05-14.
- Browsers: ES2022 baseline — Chrome 94+, Firefox 93+, Safari 15+. Covers ~96% of global usage per caniuse "es2022" feature group.
- TypeScript: ≥5.0 (declaration map + const type parameters). Lower versions silently typecheck but lose const-narrowing on enum literals.

---

## 3. Authentication

Same dual-auth model as Python SDK (`python-spec.md` §2):

| Context | Token type | Storage |
|---|---|---|
| Browser, signed-in Clerk user | Clerk JWT | Clerk session — `useAuth().getToken()` |
| Browser, anonymous public read (rare) | `agibridge_pk_<base64>` | Public env var (`NEXT_PUBLIC_AGIBRIDGE_PUBLISHABLE_KEY`) |
| Node.js / edge runtime | `agibridge_sk_<base64>` | Server-side env var, never bundled |

**Header**: `Authorization: Bearer <token>` for all three. Backend (D5+) discriminates by token shape.

**Browser secret-key safety**: SDK constructor throws `AgibridgeError` if `agibridge_sk_...` is detected in a browser context (`typeof window !== 'undefined'`). Modeled on Stripe.js refusing secret keys in the browser (`https://stripe.com/docs/js/initializing`, accessed 2026-05-14). This is a defense-in-depth check — primary prevention is bundler env-var discipline, but the runtime check catches the "accidentally inlined sk via Next.js misconfigured `NEXT_PUBLIC_`" failure mode.

**Recommended browser pattern**: NEVER ship secret keys to the browser. Instead, mint a short-lived token via Clerk JWT on the user's behalf:

```ts
// In a Next.js Route Handler / Server Action:
import { auth } from '@clerk/nextjs/server';

export async function POST(req: Request) {
  const { getToken } = auth();
  const jwt = await getToken();              // Clerk-signed JWT, ~60s TTL, auto-refresh
  // Forward jwt as Authorization to agibridge backend
}
```

For programmatic integrations that DON'T have a Clerk session (CI jobs, server cron), use the secret key server-side only.

---

## 4. Idiomatic TypeScript surface

**Design principles**:
1. **Types-first**. Every public method has a typed input and a typed output; no `any`, no `unknown` leaking past the SDK boundary.
2. **No classes for value objects**. `Job`, `PresignUploadResponse` etc. are interfaces, not classes. The `Agibridge` client itself is a class (auth state + base URL) but everything it returns is a plain object.
3. **Promise-based, no callback variants**. Async/await is the only style.
4. **No mutable client state**. `client.apiKey` is readonly; rotating keys means new client instance. Predictable concurrency behavior.

### 4.1 Browser usage (Next.js / Vite)

```ts
import { Agibridge } from 'agibridge';
import { useAuth } from '@clerk/clerk-react';

const { getToken } = useAuth();
const client = new Agibridge({
  baseUrl: 'https://api.agibridge.com',
  authProvider: getToken,    // function returning Promise<string | null>
});

// 1. Presign upload
const presign = await client.jobs.presignUpload({
  filename: 'agibot_world_episodes_0_99.tar.gz',
  sizeBytes: 2_400_000_000,
});

// 2. Upload via XHR (browser-direct to R2)
await client.jobs.uploadBrowser({
  presign,
  file: fileInputRef.current.files[0],
  onProgress: (pct) => setUploadPct(pct),  // optional progress callback
});

// 3. Start conversion
const job = await client.jobs.start({ jobId: presign.jobId });

// 4. Poll until done (with TanStack Query — see §10)
const result = await client.jobs.waitUntilDone({ jobId: presign.jobId });

// 5. Trigger download
const download = await client.jobs.presignDownload({ jobId: result.id });
window.location.href = download.downloadUrl;  // browser handles file save
```

### 4.2 Node.js usage

```ts
import { Agibridge } from 'agibridge';
import { createReadStream, createWriteStream } from 'node:fs';
import { stat } from 'node:fs/promises';

const client = new Agibridge({
  apiKey: process.env.AGIBRIDGE_API_KEY!,
});

const { size } = await stat('./dataset.tar.gz');
const presign = await client.jobs.presignUpload({
  filename: 'dataset.tar.gz',
  sizeBytes: size,
});

await client.jobs.uploadNode({
  presign,
  stream: createReadStream('./dataset.tar.gz'),
  contentLength: size,
});

await client.jobs.start({ jobId: presign.jobId });
const job = await client.jobs.waitUntilDone({ jobId: presign.jobId, timeoutMs: 1800_000 });

const download = await client.jobs.presignDownload({ jobId: job.id });
await client.jobs.downloadNode({
  presign: download,
  stream: createWriteStream('./out.tar.gz'),
});
```

### 4.3 Endpoint surface

Matches `D4_specs.md` §3 amendment #3 — 5 MVP endpoints. SDK methods are camelCase; API paths are snake_case.

| API endpoint | SDK method | Returns |
|---|---|---|
| `POST /api/v1/jobs/presign-upload` | `client.jobs.presignUpload(req)` | `Promise<PresignUploadResponse>` |
| `POST /api/v1/jobs` | `client.jobs.start(req)` | `Promise<Job>` |
| `GET /api/v1/jobs` | `client.jobs.list(opts?)` | `AsyncIterableIterator<Job>` + page-at-a-time form |
| `GET /api/v1/jobs/{job_id}` | `client.jobs.get({ jobId })` | `Promise<Job>` |
| `GET /api/v1/jobs/{job_id}/presign-download` | `client.jobs.presignDownload({ jobId })` | `Promise<PresignDownloadResponse>` |

**Bundled helpers**:
- `client.jobs.uploadBrowser({ presign, file, onProgress })` — XHR (not fetch) because `fetch` upload-progress event is still not in Chrome stable as of 2026-05-14 (per `D4_rehydration_spec.md` §2.2).
- `client.jobs.uploadNode({ presign, stream, contentLength })` — `undici` Pool-aware streaming PUT.
- `client.jobs.downloadBrowser({ presign })` — sets `window.location.href`; alternative `client.jobs.downloadBytes(presign)` returns `ReadableStream` for in-memory processing.
- `client.jobs.downloadNode({ presign, stream })` — pipes R2 response into writable.
- `client.jobs.waitUntilDone({ jobId, timeoutMs, pollIntervalMs })` — polls; respects `D4_specs.md` §3 amendment #2 rate-limit budget (30 req/min/org).

---

## 5. Type definitions — auto-gen from OpenAPI

**Recommendation**: `openapi-typescript` (not `@hey-api/openapi-ts`).

**Reasoning**:
- `openapi-typescript` 7.13.0 (verified npm 2026-05-14) generates type-only output: `interface paths { ... }` from OpenAPI spec. Zero runtime code, zero bundle impact. We own the transport layer; codegen owns the types. (`https://openapi-ts.dev/`, accessed 2026-05-14)
- `@hey-api/openapi-ts` 0.97.1 (verified npm 2026-05-14) generates a full client including fetch wrappers — duplicates work we want to own (auth header injection, retry logic, error envelope parsing). Output is opinionated and changes between minor versions.
- The Stripe Node SDK generates types from its own internal OpenAPI source (`https://github.com/stripe/stripe-node`, accessed 2026-05-14); types-only generation is the industry pattern.

**Build step** (`package.json`):

```json
{
  "scripts": {
    "build:types": "openapi-typescript ../app/openapi.json -o src/_types.ts"
  }
}
```

CI runs `npm run build:types` on every backend OpenAPI change. Generated `_types.ts` is checked in — review diffs in PRs.

**Hand-written wrappers**: the SDK re-exports `Job`, `PresignUploadResponse` etc. from `_types.ts` with friendlier names. Codegen output is `components['schemas']['Job']`; SDK exports `Job` directly.

---

## 6. Error handling — discriminated union

**Decision**: typed errors via class hierarchy with `name` as discriminant. NOT `Result<T, E>`.

**Reasoning**:
- TypeScript ecosystem convention is exceptions, not Result monads. Every major SDK (Stripe, Clerk, Supabase, Anthropic) throws. `neverthrow`-style Results read as out-of-place in user code.
- Class-based errors compose with `instanceof` narrowing AND with discriminated union narrowing on `.name`. Both ergonomic patterns work.

```ts
export class AgibridgeError extends Error {
  override name = 'AgibridgeError';
  constructor(
    message: string,
    public readonly statusCode: number | null,
    public readonly code: string | null,        // e.g. 'rate_limit_exceeded'
    public readonly suggestion: string | null,
    public readonly requestId: string | null,
  ) {
    super(message);
  }
}

export class AgibridgeAuthError extends AgibridgeError {
  override name = 'AgibridgeAuthError' as const;
}
export class AgibridgeRateLimitError extends AgibridgeError {
  override name = 'AgibridgeRateLimitError' as const;
  public readonly retryAfterSec: number;
}
export class AgibridgeNotFoundError extends AgibridgeError {
  override name = 'AgibridgeNotFoundError' as const;
}
export class AgibridgeValidationError extends AgibridgeError {
  override name = 'AgibridgeValidationError' as const;
}
export class AgibridgeAPIError extends AgibridgeError {
  override name = 'AgibridgeAPIError' as const;
}
export class AgibridgeJobFailedError extends AgibridgeError {
  override name = 'AgibridgeJobFailedError' as const;
  public readonly job: Job;
}
export class AgibridgeTimeoutError extends AgibridgeError {
  override name = 'AgibridgeTimeoutError' as const;
  public readonly jobId: string;
}

// Usage in caller (instanceof — runtime-safe):
try {
  await client.jobs.start({ jobId });
} catch (e) {
  if (e instanceof AgibridgeRateLimitError) {
    await sleep(e.retryAfterSec * 1000);
    return retry();
  }
  if (e instanceof AgibridgeAuthError) {
    redirectToSignIn();
    return;
  }
  throw e;
}

// Usage with .name (discriminated narrowing — Sentry-friendly):
catch (e) {
  if (e instanceof AgibridgeError) {
    Sentry.captureException(e, { tags: { errorName: e.name } });
  }
}
```

**Envelope parsing**: backend returns FastAPI `{detail: {code, message, suggestion}}`. SDK preserves all three fields, exposed on every error subtype. Same shape as Python SDK (`python-spec.md` §5).

---

## 7. Fetch implementation — Web standard `fetch`, no axios

**Decision**: Web `fetch` API only. No `axios`, no `node-fetch`, no `cross-fetch` shim.

**Reasoning**:
- Node 20+ ships `fetch` natively (`https://nodejs.org/en/about/previous-releases`, accessed 2026-05-14 — `fetch` stable since Node 18, no flag needed since 21).
- Browsers ship `fetch` since 2015.
- Edge runtimes (Cloudflare Workers, Deno, Bun, Vercel Edge) implement Web `fetch` as their native primitive.
- `axios` adds ~13 KB gzipped + a duplicate transport layer that doesn't tree-shake.

**XHR exception**: `uploadBrowser()` uses `XMLHttpRequest` not `fetch` because `fetch` upload-progress events are not in Chrome stable as of 2026-05-14 (`https://developer.mozilla.org/en-US/docs/Web/API/Streams_API/Using_readable_byte_streams`, accessed 2026-05-14 — `ReadableStream.from(blob).pipeThrough(...)` is supported but progress instrumentation around it remains brittle). `D4_rehydration_spec.md` §2.2 + §7 item #6 documents this constraint authoritatively. Node uploads use `undici` streaming (built into Node 20+).

**Retry strategy**: same as Python SDK (`python-spec.md` §6). Inline retry helper, no `axios-retry` or `p-retry` dep. ~40 lines of well-tested code.

---

## 8. Bundle size

**Targets**:
- Browser client core: ≤5 KB gzipped (excluding types — types are erased at compile time, zero runtime cost).
- Node client core: ≤8 KB gzipped (includes `undici` streaming helpers; node-only entry doesn't ship to browser).

**Enforcement**: `size-limit` (`https://github.com/ai/size-limit`, accessed 2026-05-14) configured in CI; PR fails if browser entry exceeds 5 KB.

**Tree-shaking guarantees**:
- ESM-only entry for browser (`"exports": { "browser": "./dist/browser.mjs" }`).
- Single named export per file. No barrel files that re-export everything.
- No top-level side effects. `"sideEffects": false` in `package.json`.

**Stripe.js by comparison**: stripe.js core is ~30 KB gzipped (`https://docs.stripe.com/js`, accessed 2026-05-14) because it includes payment-form rendering. agibridge has no UI to ship — pure HTTP wrapping should be 1/6 the size.

---

## 9. Module format — ESM + CJS dual

**`package.json` exports**:

```json
{
  "name": "agibridge",
  "version": "0.1.0",
  "type": "module",
  "main": "./dist/index.cjs",
  "module": "./dist/index.mjs",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "browser": "./dist/browser.mjs",
      "import": "./dist/index.mjs",
      "require": "./dist/index.cjs"
    },
    "./webhook": {
      "types": "./dist/webhook.d.ts",
      "import": "./dist/webhook.mjs",
      "require": "./dist/webhook.cjs"
    }
  },
  "sideEffects": false,
  "files": ["dist"]
}
```

**Why dual**:
- `import { Agibridge } from 'agibridge'` (ESM) — Next.js, Vite, modern Node.
- `const { Agibridge } = require('agibridge')` (CJS) — legacy Node scripts, Webpack 4 holdouts.
- `"browser"` condition swaps the Node-only `undici` streaming code for the browser XHR variant — keeps bundle size on target.

**Build tool**: `tsup` (`https://tsup.egoist.dev/`, accessed 2026-05-14). Single config, dual output, automatic `.d.ts` generation. Stripe Node uses a custom Rollup config; `tsup` is the modern equivalent and reduces our build maintenance.

---

## 10. Examples

Three cookbook examples in `examples/`:

### `examples/01-nextjs-server-action.ts`
Next.js 15 Server Action: upload presign in the browser, `fetch` PUT to R2 from the client, then a Server Action calls `client.jobs.start()` server-side using the user's Clerk JWT (forwarded via `auth().getToken()`). Demonstrates: browser/server token-passing pattern, Clerk + agibridge interop. ~80 lines.

### `examples/02-react-query.tsx`
TanStack Query v5 integration (matches `D4_rehydration_spec.md` §4): `useQuery({ queryKey: ['jobs'], queryFn: () => client.jobs.list().next() })` with polling cadences from `D4_specs.md` §3 amendment #2 (3 s active, 30 s idle). Demonstrates: terminal-state polling halt via `refetchInterval: (q) => isTerminal(q) ? false : 3000`, optimistic updates on `start()`. ~150 lines.

### `examples/03-node-batch.ts`
Node.js 20 batch script: iterate a folder of `.tar.gz` files, convert each, write LeRobot v3 outputs to `./out/`. Demonstrates: `for await (const job of client.jobs.list())` async iteration, `AgibridgeJobFailedError` handling, exit-code-driven flow for cron / CI scheduling, concurrency-bounded with `p-limit`. ~100 lines.

Every example has the same header convention as Python examples (`python-spec.md` §9): tested-against-commit-sha line enforced by CI 30-day staleness gate.

---

## 11. Distribution

**npm primary**: `npm publish --access public`. Trigger: `v*` tag push via GitHub Actions, OIDC (no npm token in CI secret — npm Trusted Publisher beta `https://docs.npmjs.com/trusted-publishers`, accessed 2026-05-14, currently behind feature flag but generally available signal expected mid-2026).

**Changesets workflow** (`https://github.com/changesets/changesets`, accessed 2026-05-14): every PR with user-facing change adds a `.changeset/*.md` file. CI bot opens "Version Packages" PR aggregating changesets; merging it triggers npm publish. Industry standard for monorepo-ready release flow even though SDK is single-package today.

**JSR consideration** (`https://jsr.io/`, accessed 2026-05-14): JSR is the npm-ecosystem-ESM-only registry favored by Deno-native code. Recommend dual-publishing to npm AND JSR at v0.2+ ONCE customer demand from Deno/Bun users surfaces. NOT at v0.1 — JSR has zero npm-CJS users, doubling release surface for theoretical demand fails the verify-or-drop discipline.

**Versioning**: SemVer. Mirrors Python SDK trajectory (`python-spec.md` §11):
- `0.1.x` — API key contract phase
- `1.0.0` — first paying customer using SDK in production + 30-day stable surface

---

## 12. README structure

Identical Cycle X voice anchor as Python (`python-spec.md` §12). Five sections:

1. **What this is** — 2 sentences.
2. **Install + first call** — `npm install agibridge` + a runnable 10-line snippet.
3. **Concepts** — presigned uploads / job status machine / org scoping. Same three concepts as Python — cross-SDK consistency reduces user cognitive load.
4. **Reference** — methods table, link to `https://docs.agibridge.com/ts` (Docusaurus per DR-018 default #4).
5. **Cookbook** — links to three `examples/` scripts.

---

## 13. Compatibility matrix (summary)

| Runtime | Min version | Tested? |
|---|---|---|
| Node.js | 20.0 LTS | yes (CI matrix: 20, 22) |
| Bun | 1.0 | smoke test in CI |
| Deno | 1.40 | smoke test in CI |
| Chrome | 94 (ES2022) | smoke test via Playwright |
| Firefox | 93 | smoke test via Playwright |
| Safari | 15 | smoke test via Playwright (macOS runner) |
| TypeScript | 5.0 | declared in `peerDependencies` |
| Next.js | 14.0 | tested against 14 and 15 |
| Vite | 5.0 | tested |

Node 18 NOT supported (EOL April 2025). Documented in CHANGELOG `0.1.0` notes.

---

## 14. Open [DECISION NEEDED]

**None blocking spec authoring**. Three surface-level choices below are spec-author defaults:

1. **JSR co-publishing at v0.1**: spec defaults to npm-only at v0.1, JSR consideration at v0.2 once Deno/Bun demand verifies. Alternative is dual-publish at v0.1 — adds ~2 hours release-config work for unclear benefit at zero current users.

2. **Discriminated union via `name` literal vs `tag` field**: spec defaults to `name` as discriminant (standard JS Error pattern + `instanceof`-friendly). Alternative `{ tag: 'rateLimit', retryAfter: 30 }` shape is more "functional" but loses `instanceof` ergonomics. Recommend `name`.

3. **`uploadBrowser` exposes `XMLHttpRequest` or `fetch` with cancellation only**: spec defaults to XHR for progress events. Alternative is `fetch` without progress, leaving progress UX to consumer. Recommend XHR — progress is the #1 missing-feature complaint about fetch-based upload SDKs per Stripe Elements docs (`https://stripe.com/docs/elements`, accessed 2026-05-14, indirect signal).

---

## 15. Sources

- `/Users/allenwu/.plans/agibridge-2026/CLAUDE.md` (charter)
- `/Users/allenwu/.plans/agibridge-2026/DECISIONS.md` DR-005 Clerk, DR-007 R2, DR-008 Stripe
- `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/dispatches/D4_specs.md` §3 cross-brief amendments
- `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/frontend/D4_rehydration_spec.md` §3 endpoint inventory + §2.2 XHR-vs-fetch upload-progress constraint + §7 item #6
- `/Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/marketing/site-copy/homepage.md` Cycle X voice anchor
- https://registry.npmjs.org/agibridge — HTTP 404 (available), accessed 2026-05-14
- https://registry.npmjs.org/agibridge-sdk — HTTP 404 (available), accessed 2026-05-14
- https://registry.npmjs.org/@agibridge/sdk — HTTP 404 (available), accessed 2026-05-14
- https://nodejs.org/en/about/previous-releases — Node.js LTS schedule (Node 18 EOL April 2025; Node 20 LTS), accessed 2026-05-14
- https://openapi-ts.dev/ — `openapi-typescript` 7.13.0 (verified npm), accessed 2026-05-14
- https://registry.npmjs.org/@hey-api/openapi-ts — alternative (rejected), 0.97.1, accessed 2026-05-14
- https://github.com/stripe/stripe-node — Stripe Node SDK as reference (types-only codegen pattern), accessed 2026-05-14
- https://registry.npmjs.org/stripe — Stripe Node 22.1.1, accessed 2026-05-14
- https://stripe.com/docs/js/initializing — Stripe.js browser key-shape check, accessed 2026-05-14
- https://docs.stripe.com/js — Stripe.js bundle size reference (~30 KB gzipped), accessed 2026-05-14
- https://stripe.com/docs/elements — Stripe Elements UX patterns (indirect XHR-progress signal), accessed 2026-05-14
- https://clerk.com/docs/quickstarts/react — Clerk React quickstart + `useAuth().getToken()`, accessed 2026-05-14
- https://registry.npmjs.org/@clerk/clerk-js — `@clerk/clerk-js` 6.10.1, accessed 2026-05-14
- https://github.com/ai/size-limit — bundle-size CI tool, accessed 2026-05-14
- https://tsup.egoist.dev/ — dual ESM+CJS build tool, accessed 2026-05-14
- https://github.com/changesets/changesets — release workflow, accessed 2026-05-14
- https://docs.npmjs.com/trusted-publishers — npm OIDC publish, accessed 2026-05-14
- https://jsr.io/ — ESM-first registry (deferred to v0.2+), accessed 2026-05-14
- https://developer.mozilla.org/en-US/docs/Web/API/Streams_API/Using_readable_byte_streams — fetch upload-progress constraint, accessed 2026-05-14

---

**Word count** (excluding meta-header, sources, code samples): ~2,000 words — within 1,500–2,500 target.
