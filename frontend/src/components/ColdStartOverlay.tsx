/**
 * Cold-start overlay. Phase D A2.6 (resolved):
 *   "Show 'Space is warming up — first run takes ~60s' overlay if /health
 *   returns 5xx OR doesn't respond within 5s on initial page load. Dismiss
 *   when /health is ok:true."
 */

import { WARMING_UP_BODY, WARMING_UP_TITLE } from "../lib/copy";
import type { ColdStartState } from "../lib/useColdStart";

interface Props {
  state: ColdStartState;
}

export function ColdStartOverlay({ state }: Props) {
  if (state !== "warming") return null;
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-0 top-0 z-50 border-b border-amber-300 bg-amber-100 px-4 py-3 text-sm text-amber-900 shadow-sm dark:border-amber-800 dark:bg-amber-950/80 dark:text-amber-100"
    >
      <div className="mx-auto flex max-w-3xl items-center gap-3">
        <span aria-hidden className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-500" />
        <p>
          <strong>{WARMING_UP_TITLE}</strong> — {WARMING_UP_BODY}
        </p>
      </div>
    </div>
  );
}
