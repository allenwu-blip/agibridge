/**
 * Empty state — Netron-style A2 reference. Phase D A2.5 (resolved):
 *   3 pre-loaded sample dataset URLs visible on first page load.
 *
 * v0 doesn't ship the `?dataset=<HF_ID>` deep-link path (Rerun A4) — that
 * needs a backend HF Hub fetch endpoint and is out of brief scope. Each
 * sample shows the HF Hub URL as a hint for the user to download themselves.
 */

import type { SampleDataset } from "../lib/copy";
import { SAMPLE_DATASETS } from "../lib/copy";
import type { Format } from "../lib/types";

interface Props {
  onPickSample: (sample: { fromFormat: Format; toFormat: Format }) => void;
}

export function EmptyState({ onPickSample }: Props) {
  return (
    <section
      aria-labelledby="empty-state-heading"
      className="rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900/40"
    >
      <h2 id="empty-state-heading" className="text-base font-semibold">
        Don&rsquo;t have an archive handy?
      </h2>
      <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
        Three small samples Allen has tested on this Space. Click one to
        prefill the format selectors, then download the dataset from
        HuggingFace and drop it above.
      </p>
      <ul className="mt-3 space-y-2">
        {SAMPLE_DATASETS.map((sample) => (
          <SampleRow key={sample.id} sample={sample} onPick={onPickSample} />
        ))}
      </ul>
    </section>
  );
}

function SampleRow({
  sample,
  onPick,
}: {
  sample: SampleDataset;
  onPick: (s: { fromFormat: Format; toFormat: Format }) => void;
}) {
  return (
    <li className="rounded border border-stone-200 p-3 dark:border-stone-800">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <button
          type="button"
          onClick={() =>
            onPick({ fromFormat: sample.fromFormat, toFormat: sample.toFormat })
          }
          className="font-mono text-sm font-medium text-indigo-700 hover:underline dark:text-indigo-300"
        >
          {sample.label}
        </button>
        <span className="text-xs text-stone-500 dark:text-stone-400">
          {sample.fromFormat} → {sample.toFormat}
        </span>
      </div>
      <p className="mt-1 text-xs text-stone-600 dark:text-stone-400">{sample.note}</p>
      <a
        href={sample.hubUrl}
        target="_blank"
        rel="noreferrer"
        className="mt-1 inline-block text-xs text-stone-500 underline hover:text-stone-700 dark:text-stone-500 dark:hover:text-stone-300"
      >
        {sample.hubUrl}
      </a>
    </li>
  );
}
