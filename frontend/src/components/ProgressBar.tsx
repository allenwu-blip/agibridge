/**
 * Progress bar with explicit "(estimate)" annotation.
 *
 * DoD #8 (Phase D A2.3): "Progress bar shows percentage with explicit
 * (estimate) annotation visible. NEVER displays 100% before state == done
 * (clamp at 99 during running/validating)."
 *
 * Spec §2.2.1 honesty constraints:
 *   1. Frontend renders the percentage with explicit "(estimate)" annotation.
 *   2. Wrapper clamps to 99% while state in {running, validating}.
 *   3. Status payload includes estimated_progress_pct as int 0..99 when state
 *      in {running, validating}, else null.
 *
 * Backend implementation: app/api/status.py:63-79 (_estimated_progress_pct
 * clamps at 99 and returns None unless state is running/validating).
 *
 * The component DOUBLE-CHECKS the clamp client-side: even if a malformed
 * payload returned 100 with state != done, we cap at 99. Belt-and-braces.
 */

import type { ConversionState } from "../lib/types";

interface Props {
  state: ConversionState;
  /** From StatusResponse.estimated_progress_pct. May be null. */
  estimatedProgressPct: number | null;
}

export function ProgressBar({ state, estimatedProgressPct }: Props) {
  const isTerminal = state === "done" || state === "failed" || state === "expired";
  const isDone = state === "done";

  // Clamp client-side per spec §2.2.1 honesty constraint #2 — backend already
  // clamps but we double-check for defense in depth.
  let displayPct: number;
  let annotation: string;
  if (isDone) {
    displayPct = 100;
    annotation = "complete";
  } else if (isTerminal) {
    // failed | expired — show whatever the last estimate was, do NOT round to 100.
    displayPct = Math.min(estimatedProgressPct ?? 0, 99);
    annotation = state === "failed" ? "stopped" : "expired";
  } else {
    // pending | extracting | running | validating — clamp at 99 even if backend slipped.
    const raw = estimatedProgressPct ?? 0;
    displayPct = Math.max(0, Math.min(99, raw));
    annotation = "estimate";
  }

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={displayPct}
      aria-valuetext={`${displayPct}% (${annotation})`}
      className="space-y-1"
    >
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-medium">
          {displayPct}%{" "}
          <span className="text-xs font-normal text-stone-600 dark:text-stone-400">
            ({annotation})
          </span>
        </span>
        <span className="text-xs uppercase tracking-wide text-stone-600 dark:text-stone-400">
          {state}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800">
        <div
          className={
            isDone
              ? "h-full bg-emerald-500 transition-[width] duration-500"
              : state === "failed" || state === "expired"
                ? "h-full bg-red-500 transition-[width] duration-500"
                : "h-full bg-indigo-500 transition-[width] duration-500"
          }
          style={{ width: `${displayPct}%` }}
        />
      </div>
    </div>
  );
}
