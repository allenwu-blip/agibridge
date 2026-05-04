/**
 * Error toast component (DoD #9 / C5 propagation).
 *
 * For three specific wrapper-side codes the toast strings are VERBATIM from
 * agibridge/landing/voice-guide.md Surface 2:
 *   - converter_rejected_input
 *   - oom_suspected
 *   - conversion_timeout
 *
 * For other failure codes (busy / invalid_format_pair / archive_too_large /
 * unsupported_archive_type / mime_spoofed / converter_crashed /
 * validation_failed_after_convert / disk_full / download_expired /
 * session_not_found) we render the wrapper's error.message + suggestion as
 * received from spec §5 mapping table — those don't have a "voice register"
 * Surface 2 entry yet, so we trust the spec text directly. If a future C-
 * propagation adds more verbatim strings to voice-guide, swap in here.
 */

import {
  TOAST_CONVERTER_REJECTED_INPUT_LIB_MSG_LABEL,
  TOAST_CONVERTER_REJECTED_INPUT_PREFIX,
  TOAST_CONVERTER_REJECTED_INPUT_SUGGESTION_LABEL,
  TOAST_GH_ISSUES_URL,
  TOAST_OOM_BODY_LINK_TEXT,
  TOAST_OOM_BODY_POST_LINK,
  TOAST_OOM_BODY_PRE_LINK,
  TOAST_OOM_PREFIX,
  TOAST_TIMEOUT_BODY,
  TOAST_TIMEOUT_PREFIX,
} from "../lib/copy";
import type { ConversionError } from "../lib/types";

interface Props {
  /** May be a wrapper-side ConversionError (status.error) or an upload-time
   * ApiErrorDetail. Same shape: code + message + suggestion. */
  error: { code: string; message: string; suggestion: string | null };
  sessionId: string | null;
  onDismiss?: () => void;
}

export function ErrorToast({ error, sessionId, onDismiss }: Props) {
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
    >
      {renderBody(error, sessionId)}
      {onDismiss && (
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-md border border-red-300 px-3 py-1 text-xs font-medium hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900/40"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Code-ish span — used for `<error.message>` placeholders and lib-quoted text.
 */
function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded bg-red-100 px-1 py-0.5 font-mono text-[0.85em] text-red-900 dark:bg-red-900/40 dark:text-red-100">
      {children}
    </code>
  );
}

function GhLink() {
  return (
    <a
      href={TOAST_GH_ISSUES_URL}
      target="_blank"
      rel="noreferrer"
      className="font-medium underline hover:no-underline"
    >
      embodied-data GitHub
    </a>
  );
}

function GhIssuesLink() {
  return (
    <a
      href={TOAST_GH_ISSUES_URL}
      target="_blank"
      rel="noreferrer"
      className="font-medium underline hover:no-underline"
    >
      {TOAST_OOM_BODY_LINK_TEXT}
    </a>
  );
}

/**
 * Render the toast body. The three voice-guide Surface 2 codes branch into
 * verbatim strings; everything else falls through to the spec §5 message.
 */
function renderBody(
  error: { code: string; message: string; suggestion: string | null },
  sessionId: string | null,
): React.ReactNode {
  switch (error.code) {
    case "converter_rejected_input":
      // Voice guide Surface 2 line 1.
      return (
        <div className="space-y-2">
          <p>
            <strong>{TOAST_CONVERTER_REJECTED_INPUT_PREFIX}</strong>
          </p>
          <p>
            {TOAST_CONVERTER_REJECTED_INPUT_LIB_MSG_LABEL}{" "}
            <Code>{error.message}</Code>.{" "}
            {TOAST_CONVERTER_REJECTED_INPUT_SUGGESTION_LABEL}{" "}
            <Code>{error.suggestion ?? "—"}</Code>.{" "}
            If this looks like a bug, open an issue on the <GhLink /> with
            the session_id and the lib message — contributors welcome.
          </p>
          {sessionId && <SessionIdChip sessionId={sessionId} />}
        </div>
      );
    case "oom_suspected":
      // Voice guide Surface 2 line 2.
      return (
        <div className="space-y-2">
          <p>
            <strong>{TOAST_OOM_PREFIX}</strong>
          </p>
          <p>
            {TOAST_OOM_BODY_PRE_LINK}
            <GhIssuesLink />
            {TOAST_OOM_BODY_POST_LINK}
          </p>
          {sessionId && <SessionIdChip sessionId={sessionId} />}
        </div>
      );
    case "conversion_timeout":
      // Voice guide Surface 2 line 3.
      return (
        <div className="space-y-2">
          <p>
            <strong>{TOAST_TIMEOUT_PREFIX}</strong>
          </p>
          <p>{TOAST_TIMEOUT_BODY}</p>
          {sessionId && <SessionIdChip sessionId={sessionId} />}
        </div>
      );
    default:
      // Other codes: trust the wrapper's spec §5 message + suggestion.
      return (
        <div className="space-y-2">
          <p>
            <strong>{error.message}</strong>
          </p>
          {error.suggestion && <p>{error.suggestion}</p>}
          <p className="text-xs">
            Code: <Code>{error.code}</Code>
          </p>
          {sessionId && <SessionIdChip sessionId={sessionId} />}
        </div>
      );
  }
}

function SessionIdChip({ sessionId }: { sessionId: string }) {
  return (
    <p className="text-xs">
      session_id: <Code>{sessionId}</Code>
    </p>
  );
}

/**
 * Render a non-conversion error from a ConversionError envelope. Type-narrowing
 * helper.
 */
export function toApiError(err: ConversionError): {
  code: string;
  message: string;
  suggestion: string | null;
} {
  return { code: err.code, message: err.message, suggestion: err.suggestion };
}
