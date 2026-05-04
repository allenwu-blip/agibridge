/**
 * Beta-coverage banner (DoD #4 — verbatim copy from spec §5 line 342).
 *
 * Phase D A2.4 (resolved): always-visible info-bar above the upload form when
 * `from_format=agibot`. The body itself is ALL one paragraph copy/paste from
 * spec — no paraphrasing.
 *
 * Render strategy: backticks (`) become inline <code> spans for readability,
 * but the textual content is the same string. RC will diff text, not markup.
 */

import { BETA_BANNER_BODY } from "../lib/copy";

/** Render `\`text\`` segments as <code> spans, leaving the rest as plain text. */
function renderInlineCode(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const regex = /`([^`]+)`/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <code
        key={`c${key++}`}
        className="rounded bg-amber-100 px-1 py-0.5 font-mono text-[0.85em] text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
      >
        {match[1]}
      </code>,
    );
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

interface Props {
  visible: boolean;
}

export function BetaBanner({ visible }: Props) {
  if (!visible) return null;
  return (
    <aside
      role="region"
      aria-label="AgiBot Beta coverage notice"
      className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm leading-relaxed text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100"
    >
      <p>{renderInlineCode(BETA_BANNER_BODY)}</p>
    </aside>
  );
}
