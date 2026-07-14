/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Clerk publishable key (instance relevant-mosquito-93.clerk.accounts.dev). */
  readonly VITE_CLERK_PUBLISHABLE_KEY: string;
  /** Stripe publishable key (test mode at MVP). */
  readonly VITE_STRIPE_PUBLISHABLE_KEY: string;
  /** Backend origin, e.g. https://allenwu06-agibridge.hf.space (no trailing /api/v1). */
  readonly VITE_API_BASE_URL: string;
  /** Sentry browser DSN; optional (no-op if absent). */
  readonly VITE_SENTRY_DSN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
