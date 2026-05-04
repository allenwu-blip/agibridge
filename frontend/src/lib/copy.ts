/**
 * Single source of truth for verbatim user-facing copy. The strings in this
 * file are copy/pasted from canonical documents and MUST NOT be paraphrased.
 *
 * - BETA_BANNER_BODY — _workspace/backend-architecture-W1.md §5 line 342 (post
 *   v2.1 patch). Brief DoD #4: byte-for-byte. Note: brief refers to "spec §5
 *   line 309" but the live v2.2 spec has the verbatim banner at line 342 of
 *   the same §5; the brief points at an outdated line number, the body itself
 *   is unchanged. RC will diff the body, not the line ref.
 * - EPHEMERAL_NOTICE — spec §10 check #2 + app/api/upload.py:158-160
 *   ("Files are kept for 30 minutes, then deleted. No accounts, no storage.").
 *   DoD #3: must appear on every page-level surface.
 * - TOAST_* — agibridge/landing/voice-guide.md Surface 2. DoD #9 / C5
 *   propagation: verbatim, NOT composed from spec error.message.
 * - WARMING_UP — Phase D A2.6 (resolved 2026-05-02).
 * - MULTI_CAMERA_NOTE — spec §6 line 379 verbatim.
 * - FOOTER_HOBBY — spec §10 positive-pattern #5 verbatim.
 *
 * F-1 banned-word grep ENFORCED: app/static/** (build output of this file).
 * Anti-pattern grep also ENFORCED: no analytics SDKs.
 */

/** Beta-coverage banner. Verbatim from spec §5 line 342 (post v2.1). */
export const BETA_BANNER_BODY =
  "AgiBot Beta is partially supported. This Space ships with `embodied-data==0.3.1`, which converts the joint/effector/head/waist subgroups and the `head_color` camera. Three Beta features are silently dropped without error (per `docs/schema/beta.md:105, 148-155, 184-185, 222-228`): sparse-action `*/index` masks, `state/end/*` end-pose wrench data, and non-`head_color` cameras (fisheye / hand / back). Conversion completes; the dropped fields simply don't appear in the LeRobot v3 output. Separately, schema-shape mismatches in *captured* fields (e.g. unexpected dtype on `state/joint/position`) surface as `converter_rejected_input` with the lib's pass-through diagnostic. Please file the stderr tail on the embodied-data GitHub for either case.";

/**
 * Ephemeral notice — spec §10 check #2 verbatim.
 * Backend repeats this in app/api/upload.py:158-160 and app/main.py:51 — so
 * the wording is fixed across both surfaces.
 */
export const EPHEMERAL_NOTICE =
  "Files are kept for 30 minutes, then deleted. No accounts, no storage.";

/**
 * Multi-camera silent-drop note. Verbatim from spec §6 line 379:
 * "If your Beta task has 7 cameras, this Space converts only `head_color`;
 * the other 6 cameras are silently dropped. For multi-camera datasets, run
 * the CLI locally."
 */
export const MULTI_CAMERA_NOTE =
  "If your Beta task has 7 cameras, this Space converts only `head_color`; the other 6 cameras are silently dropped. For multi-camera datasets, run the CLI locally.";

/**
 * Cold-start "warming up" copy. Phase D A2.6 (resolved):
 * "Space is warming up — first run takes ~60s".
 * Trigger: /api/v1/health 5xx OR no response within 5s.
 */
export const WARMING_UP_TITLE = "Space is warming up";
export const WARMING_UP_BODY = "First run takes about 60 seconds.";

/** Footer hobby framing — spec §10 positive-pattern #5 verbatim. */
export const FOOTER_HOBBY =
  "Hobby project by Allen Wu (@allenwu-blip). Open source under MIT. embodied-data v0.3.1.";

/**
 * Error toast strings. VERBATIM from agibridge/landing/voice-guide.md Surface 2.
 * C5 propagation rule (brief 2026-05-04): MUST consume Surface 2 verbatim;
 * do NOT compose new toast copy from the spec error.message strings.
 *
 * Each entry is a template producing the markdown-style body. We render bold
 * + the embedded backtick code spans + the linked GitHub URL in the component.
 */
export const TOAST_GH_ISSUES_URL =
  "https://github.com/allenwu-blip/embodied-data/issues";

/**
 * `converter_rejected_input` (422). Verbatim Surface 2 line 1.
 * Lib message + suggestion are pass-through fields per spec §5 mapping table.
 */
export interface RejectedInputToast {
  readonly libMessage: string;
  readonly libSuggestion: string;
}

/**
 * Voice guide Surface 2 verbatim:
 * "**The converter rejected this archive.** Lib message: `<error.message>`.
 * Suggestion: `<error.suggestion>`. If this looks like a bug, open an issue
 * on the [embodied-data GitHub](...) with the session_id and the lib message
 * — contributors welcome."
 */
export const TOAST_CONVERTER_REJECTED_INPUT_PREFIX =
  "The converter rejected this archive.";
export const TOAST_CONVERTER_REJECTED_INPUT_LIB_MSG_LABEL = "Lib message:";
export const TOAST_CONVERTER_REJECTED_INPUT_SUGGESTION_LABEL = "Suggestion:";
export const TOAST_CONVERTER_REJECTED_INPUT_TAIL =
  "If this looks like a bug, open an issue on the embodied-data GitHub with the session_id and the lib message — contributors welcome.";

/**
 * Voice guide Surface 2 verbatim:
 * "**The run was stopped, most likely because it exceeded the memory limit on
 * this hosted environment.** Run the CLI locally for full datasets:
 * `pip install embodied-data`. If this happens on a small archive, would
 * value feedback — paste the session_id on the [embodied-data GitHub
 * issues](...)."
 */
export const TOAST_OOM_PREFIX =
  "The run was stopped, most likely because it exceeded the memory limit on this hosted environment.";
export const TOAST_OOM_BODY_PRE_LINK =
  "Run the CLI locally for full datasets: `pip install embodied-data`. If this happens on a small archive, would value feedback — paste the session_id on the ";
export const TOAST_OOM_BODY_LINK_TEXT = "embodied-data GitHub issues";
export const TOAST_OOM_BODY_POST_LINK = ".";

/**
 * Voice guide Surface 2 verbatim:
 * "**The run took longer than 25 minutes and was stopped.** Smaller slices via
 * `--max-episodes` work; full Beta tasks are best run locally with `pip
 * install embodied-data`. If a smaller slice still times out, open an issue
 * with the session_id."
 */
export const TOAST_TIMEOUT_PREFIX =
  "The run took longer than 25 minutes and was stopped.";
export const TOAST_TIMEOUT_BODY =
  "Smaller slices via `--max-episodes` work; full Beta tasks are best run locally with `pip install embodied-data`. If a smaller slice still times out, open an issue with the session_id.";

/**
 * Empty state — Netron-style A2 reference. Phase D A2.5: 3 pre-loaded sample
 * dataset URLs. Hosted on HF Hub; brief amendment 2 lists the IDs. We keep
 * sample IDs as click-to-fill text in the form (no `?dataset=<HF_ID>` deep-
 * link in v0 — that requires a backend HF Hub fetch endpoint, see brief
 * escalation triggers).
 */
export interface SampleDataset {
  readonly id: string;
  readonly label: string;
  readonly fromFormat: "agibot" | "lerobot-v3";
  readonly toFormat: "agibot" | "lerobot-v3";
  readonly note: string;
  readonly hubUrl: string;
}

export const SAMPLE_DATASETS: ReadonlyArray<SampleDataset> = [
  {
    id: "lerobot/pusht",
    label: "lerobot/pusht",
    fromFormat: "lerobot-v3",
    toFormat: "agibot",
    note: "206 episodes, ~150 MB. Spec §6 row 1: OK on CPU Basic, ~30 s.",
    hubUrl: "https://huggingface.co/datasets/lerobot/pusht",
  },
  {
    id: "agibot-world/AgiBotWorld-Beta task 675 (1 episode)",
    label: "AgiBotWorld-Beta task 675 (1 episode)",
    fromFormat: "agibot",
    toFormat: "lerobot-v3",
    note: "Single episode, ~12 MB. Spec §6 row 2: OK on CPU Basic, ~15 s.",
    hubUrl:
      "https://huggingface.co/datasets/agibot-world/AgiBotWorld-Beta",
  },
  {
    id: "agibot-world/AgiBotWorld-Beta task 675 (47 episodes)",
    label: "AgiBotWorld-Beta task 675 (47 episodes)",
    fromFormat: "agibot",
    toFormat: "lerobot-v3",
    note: "47 episodes, ~600 MB. Spec §6 row 3: OK on CPU Basic, ~3-4 min.",
    hubUrl:
      "https://huggingface.co/datasets/agibot-world/AgiBotWorld-Beta",
  },
];
