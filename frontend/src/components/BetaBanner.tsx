/**
 * Beta-coverage banner (DoD #4 — verbatim copy from spec §5 line 342).
 *
 * Phase D A2.4 (resolved): always-visible info-bar above the upload form when
 * `from_format=agibot`. The body itself is ALL one paragraph copy/paste from
 * spec — no paraphrasing.
 *
 * Render strategy (HP-2 fix): backticks (`) become inline <code> spans;
 * **bold** segments become <strong> spans. Spec §5 line 342 uses both bold
 * and inline code; previous implementation stripped bold silently. Now both
 * are rendered. RC will diff the underlying string text against the spec.
 */

import { BETA_BANNER_BODY } from "../lib/copy";

/** Render `\`code\`` and `**bold**` segments inline; leaving the rest as plain text. */
function renderInlineMarkup(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  // Match either **bold** or `code`; bold is greedy enough to cover phrases.
  const regex = /(\*\*([^*]+?)\*\*)|(`([^`]+)`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    if (match[1]) {
      // **bold** match — match[2] is inner content
      parts.push(
        <strong key={`b${key++}`} className="font-semibold">
          {match[2]}
        </strong>,
      );
    } else {
      // `code` match — match[4] is inner content
      parts.push(
        <code
          key={`c${key++}`}
          className="rounded bg-amber-100 px-1 py-0.5 font-mono text-[0.85em] text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
        >
          {match[4]}
        </code>,
      );
    }
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
      <p>{renderInlineMarkup(BETA_BANNER_BODY)}</p>
    </aside>
  );
}
