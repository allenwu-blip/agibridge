/**
 * Drag-drop / click upload form. Spec §2.1, brief Scope IN.
 *
 * Form fields per backend's app/api/upload.py:62-66:
 *   - file (UploadFile, required) — .zip or .tar.gz
 *   - from_format (str, required) — agibot | lerobot-v3
 *   - to_format (str, required) — agibot | lerobot-v3
 *   - max_episodes (int | None) — passed through to lib's --max-episodes flag
 *
 * Client-side validation mirrors backend's _SUPPORTED_PAIRS at
 * app/api/upload.py:39 — fail fast before the multipart hits the wire.
 */

import { useCallback, useId, useRef, useState } from "react";
import type { Format } from "../lib/types";
import { SUPPORTED_PAIRS } from "../lib/types";

export interface UploadSubmission {
  file: File;
  fromFormat: Format;
  toFormat: Format;
  maxEpisodes: number | null;
}

interface Props {
  disabled: boolean;
  /** Optional initial selection — empty-state samples pre-fill via this prop. */
  initialFromFormat?: Format;
  initialToFormat?: Format;
  onSubmit: (submission: UploadSubmission) => void;
}

function isPairSupported(from: Format, to: Format): boolean {
  if (from === to) return false;
  return SUPPORTED_PAIRS.some(([f, t]) => f === from && t === to);
}

export function UploadForm({
  disabled,
  initialFromFormat = "agibot",
  initialToFormat = "lerobot-v3",
  onSubmit,
}: Props) {
  const formId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fromFormat, setFromFormat] = useState<Format>(initialFromFormat);
  const [toFormat, setToFormat] = useState<Format>(initialToFormat);
  const [maxEpisodes, setMaxEpisodes] = useState<string>("");
  const [dragActive, setDragActive] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);

  const handleFiles = useCallback((list: FileList | null) => {
    if (!list || list.length === 0) {
      setFile(null);
      return;
    }
    const f = list[0]!;
    // Accept .zip and .tar.gz only; matches backend magic-byte check at
    // app/api/upload.py:34-35 + 202-210 (_detect_archive_kind).
    const name = f.name.toLowerCase();
    if (!name.endsWith(".zip") && !name.endsWith(".tar.gz") && !name.endsWith(".tgz")) {
      setValidationError("Only .zip and .tar.gz archives are accepted here.");
      setFile(null);
      return;
    }
    setValidationError(null);
    setFile(f);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLLabelElement>) => {
      e.preventDefault();
      setDragActive(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const onSubmitForm = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!file) {
      setValidationError("Please choose a source format and a target format before uploading.");
      return;
    }
    if (!isPairSupported(fromFormat, toFormat)) {
      setValidationError(
        "This conversion direction isn't supported yet. Try AgiBot → LeRobot v3, or the reverse.",
      );
      return;
    }
    let maxEp: number | null = null;
    if (maxEpisodes.trim() !== "") {
      const parsed = parseInt(maxEpisodes, 10);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setValidationError("max_episodes must be a positive integer (or leave blank for full).");
        return;
      }
      maxEp = parsed;
    }
    setValidationError(null);
    onSubmit({ file, fromFormat, toFormat, maxEpisodes: maxEp });
  };

  return (
    <form
      onSubmit={onSubmitForm}
      aria-labelledby={`${formId}-heading`}
      className="space-y-4"
    >
      <h2 id={`${formId}-heading`} className="text-base font-semibold">
        Upload an archive
      </h2>

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
          disabled ? "pointer-events-none opacity-60" : "",
        ].join(" ")}
      >
        <span className="text-base font-medium">
          {file ? file.name : "Drop a .zip or .tar.gz, or click to choose"}
        </span>
        <span className="text-xs text-stone-600 dark:text-stone-400">
          Up to 800 MB compressed / 1.5 GB uncompressed.
        </span>
        <input
          id={`${formId}-file`}
          ref={fileInputRef}
          type="file"
          accept=".zip,.tar.gz,.tgz,application/zip,application/gzip"
          className="sr-only"
          onChange={(e) => handleFiles(e.target.files)}
          disabled={disabled}
        />
      </label>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <label htmlFor={`${formId}-from`} className="block text-sm font-medium">
            From format
          </label>
          <select
            id={`${formId}-from`}
            value={fromFormat}
            onChange={(e) => setFromFormat(e.target.value as Format)}
            disabled={disabled}
            className="block w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm shadow-sm dark:border-stone-700 dark:bg-stone-900"
          >
            <option value="agibot">agibot</option>
            <option value="lerobot-v3">lerobot-v3</option>
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor={`${formId}-to`} className="block text-sm font-medium">
            To format
          </label>
          <select
            id={`${formId}-to`}
            value={toFormat}
            onChange={(e) => setToFormat(e.target.value as Format)}
            disabled={disabled}
            className="block w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm shadow-sm dark:border-stone-700 dark:bg-stone-900"
          >
            <option value="agibot">agibot</option>
            <option value="lerobot-v3">lerobot-v3</option>
          </select>
        </div>
      </div>

      <div className="space-y-1">
        <label htmlFor={`${formId}-max`} className="block text-sm font-medium">
          max_episodes <span className="text-xs text-stone-600 dark:text-stone-400">(optional)</span>
        </label>
        <input
          id={`${formId}-max`}
          // type="text" with inputMode="numeric" — we validate range in JS so
          // we keep HTML5 form validation off this field; the spec rejects
          // negatives in the wrapper anyway. Letting the user paste/type the
          // value freely matches React Hook Form's "controlled inputs, JS
          // validation" pattern.
          type="text"
          inputMode="numeric"
          placeholder="leave blank for full dataset"
          value={maxEpisodes}
          onChange={(e) => setMaxEpisodes(e.target.value)}
          disabled={disabled}
          className="block w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm shadow-sm dark:border-stone-700 dark:bg-stone-900"
        />
        <p className="text-xs text-stone-600 dark:text-stone-400">
          Passed through to the converter's --max-episodes flag.
        </p>
      </div>

      {validationError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">
          {validationError}
        </p>
      )}

      <button
        type="submit"
        disabled={disabled || !file}
        className="inline-flex items-center justify-center rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-60"
      >
        Convert
      </button>
    </form>
  );
}
