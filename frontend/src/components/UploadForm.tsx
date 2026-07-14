/**
 * Drag-drop / click upload form.
 *
 * Refactor of frontend/v0-spa:frontend/src/components/UploadForm.tsx per
 * frontend/D4_rehydration_spec.md §2:
 *   - VERBATIM-KEEP: `handleFiles` + extension allowlist (src §2.3 / orig
 *     :53-69), `onDrop` (orig :71-78), `useId` + `fileInputRef`,
 *     `validationError` + `<p role="alert">` a11y semantics, the drag-drop
 *     label-card JSX + Tailwind dragActive classes.
 *   - ADD: `.tar` to the allowlist (§2.3 — AgiBot World ships uncompressed tar).
 *   - DROP: from/to `<select>` blocks + `max_episodes` `<input>` (§2.4).
 *   - CALLBACK: `onSubmit({...})` -> `await onPresignAndStart({file})` (§2.5);
 *     parent owns presign->PUT->create + optimistic insertion.
 *   - ADD: `<progress>` region driven by `uploadPct` (§2.4 JSX add).
 *
 * Single-direction MVP retires `isPairSupported` (§2.4); the backend still
 * needs from/to (app/api/jobs.py:80-82) so the parent supplies the fixed
 * AgiBot -> LeRobot v3 pair (matches Free-tier copy in docs/pricing-copy.md:45).
 */

import { useCallback, useId, useRef, useState } from "react";

/**
 * Free-plan upload-size ceiling, in bytes. Mirrors the backend Free-tier cap
 * `MAX_UPLOAD_BYTES[OrgTier.free]` (app/api/billing_config.py — 800 MB). This
 * is a FAIL-FAST UX guard only: the backend re-checks the real per-tier cap
 * server-side at presign time (app/api/jobs.py `presign_upload` → 413
 * `upload_too_large`). We assume the visitor's plan only for the message —
 * worst case a paid user is told "Free" but their actual presign still
 * succeeds against the higher server cap, so this never wrongly blocks them
 * (the client guard only trips above the SMALLEST plan's ceiling).
 */
export const MAX_UPLOAD_BYTES_HINT = 800 * 1024 * 1024;

interface Props {
  disabled: boolean;
  /** Parent runs presign -> XHR PUT -> create. Resolves when the job is queued. */
  onPresignAndStart: (p: { file: File }) => Promise<void>;
  /** Upload progress 0..100, or null when not uploading (§2.4). */
  uploadPct: number | null;
  /** Surfaced via the existing validationError slot (§2.5). */
  uploadError?: string | null;
}

export function UploadForm({
  disabled,
  onPresignAndStart,
  uploadPct,
  uploadError,
}: Props) {
  const formId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  // VERBATIM-KEEP from orig :53-69 — extension allowlist; `.tar` added per
  // §2.3 (AgiBot World ships uncompressed tar). The backend re-checks by
  // magic bytes server-side; this is fail-fast UX only.
  const handleFiles = useCallback((list: FileList | null) => {
    if (!list || list.length === 0) {
      setFile(null);
      return;
    }
    const f = list[0]!;
    const name = f.name.toLowerCase();
    if (
      !name.endsWith(".zip") &&
      !name.endsWith(".tar.gz") &&
      !name.endsWith(".tgz") &&
      !name.endsWith(".tar")
    ) {
      setValidationError(
        "Only .zip, .tar, .tar.gz, or .tgz archives are accepted.",
      );
      setFile(null);
      return;
    }
    // Track 4 edge-hardening: fail-fast size guard. The backend re-checks
    // the real per-tier cap at presign time (413 `upload_too_large`,
    // app/api/jobs.py); catching it here means an oversized file never even
    // starts an upload, and the message names the concrete limit instead of
    // the user watching a doomed progress bar.
    if (f.size > MAX_UPLOAD_BYTES_HINT) {
      const mb = Math.round(MAX_UPLOAD_BYTES_HINT / (1024 * 1024));
      setValidationError(
        `This file is ${Math.round(f.size / (1024 * 1024))} MB — larger than ` +
          `the ${mb} MB limit on the Free plan. Upgrade for a higher limit, ` +
          "or run the CLI locally for unlimited size.",
      );
      setFile(null);
      return;
    }
    setValidationError(null);
    setFile(f);
  }, []);

  // VERBATIM-KEEP from orig :71-78.
  const onDrop = useCallback(
    (e: React.DragEvent<HTMLLabelElement>) => {
      e.preventDefault();
      setDragActive(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const onSubmitForm = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!file) {
      setValidationError("Choose an archive to convert before uploading.");
      return;
    }
    setValidationError(null);
    await onPresignAndStart({ file });
  };

  const error = validationError ?? uploadError ?? null;
  const uploading = uploadPct !== null;

  return (
    <form
      onSubmit={onSubmitForm}
      aria-labelledby={`${formId}-heading`}
      className="space-y-4"
    >
      <h2 id={`${formId}-heading`} className="text-base font-semibold">
        Upload a dataset
      </h2>

      {/* VERBATIM-KEEP label-card region + dragActive Tailwind classes (§2.3). */}
      <label
        htmlFor={`${formId}-file`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
        className={[
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition-colors",
          dragActive
            ? "border-indigo-500 bg-indigo-50 dark:bg-indigo-950/40"
            : "border-stone-300 bg-stone-50 hover:bg-stone-100 dark:border-stone-700 dark:bg-stone-900/40 dark:hover:bg-stone-900",
          disabled || uploading ? "pointer-events-none opacity-60" : "",
        ].join(" ")}
      >
        <span className="text-base font-medium">
          {file ? file.name : "Drop an AgiBot World archive, or click to choose"}
        </span>
        <span className="text-xs text-stone-600 dark:text-stone-400">
          .zip, .tar, .tar.gz or .tgz
        </span>
        <input
          id={`${formId}-file`}
          ref={fileInputRef}
          type="file"
          accept=".zip,.tar,.tar.gz,.tgz,application/zip,application/gzip,application/x-tar"
          className="sr-only"
          onChange={(e) => handleFiles(e.target.files)}
          disabled={disabled || uploading}
        />

        {/* ADD per §2.4 — progress visible only while uploading. */}
        {uploading && (
          <div className="mt-2 w-full max-w-xs">
            <progress
              value={uploadPct ?? 0}
              max={100}
              aria-label="Upload progress"
              className="h-2 w-full"
            />
            <span className="mt-1 block text-xs text-stone-600 dark:text-stone-400">
              Uploading… {uploadPct}%
            </span>
          </div>
        )}
      </label>

      {/* VERBATIM-KEEP a11y semantics: role="alert" error slot (§2.3). */}
      {error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={disabled || !file || uploading}
        className="inline-flex items-center justify-center rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {uploading ? "Uploading…" : "Convert"}
      </button>
    </form>
  );
}
