/**
 * Footer (DoD #7 / Phase D A2.2). F-1 transparency.
 *
 * Renders `agibridge {git_sha[:7]} · embodied-data {version}` from
 * /api/v1/version (app/api/version.py:53-59).
 *
 * Also includes the spec §10 positive-pattern #5 hobby framing line (verbatim).
 *
 * No analytics, no tracking pixels; per brief anti-pattern grep.
 */

import { FOOTER_HOBBY } from "../lib/copy";
import { useVersion } from "../lib/useVersion";

export function Footer() {
  const version = useVersion();
  const sha7 = version?.agibridge_git_sha?.slice(0, 7) ?? null;
  const ed = version?.embodied_data_version ?? null;

  return (
    <footer className="mt-12 border-t border-stone-200 pt-6 text-xs text-stone-600 dark:border-stone-800 dark:text-stone-400">
      <div className="space-y-1">
        <p>{FOOTER_HOBBY}</p>
        {sha7 && ed && (
          <p>
            <span className="font-mono">
              agibridge {sha7} · embodied-data {ed}
            </span>
          </p>
        )}
        <p>
          Source:{" "}
          <a
            href="https://github.com/allenwu-blip/embodied-data"
            target="_blank"
            rel="noreferrer"
            className="underline hover:no-underline"
          >
            embodied-data on GitHub
          </a>{" "}
          · MIT.
        </p>
      </div>
    </footer>
  );
}
