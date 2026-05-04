/**
 * agibridge SPA shell.
 *
 * Surface composition:
 *   - ColdStartOverlay (Phase D A2.6)
 *   - Header
 *   - BetaBanner (Phase D A2.4: visible when from_format == agibot)
 *   - UploadForm + EmptyState side-by-side (until upload starts)
 *   - StatusPanel + ErrorToast after upload
 *   - Footer (DoD #7 — version display from /api/v1/version)
 *
 * Every page-level surface includes the spec §10 ephemeral-notice sentence
 * verbatim (DoD #3).
 */

import { useEffect, useState } from "react";
import { uploadConversion } from "./lib/api";
import { EPHEMERAL_NOTICE, MULTI_CAMERA_NOTE } from "./lib/copy";
import type {
  Format,
  StatusResponse,
  UploadFlowPhase,
} from "./lib/types";
import { useColdStart } from "./lib/useColdStart";
import { usePoller } from "./lib/usePoller";
import { BetaBanner } from "./components/BetaBanner";
import { ColdStartOverlay } from "./components/ColdStartOverlay";
import { EmptyState } from "./components/EmptyState";
import { ErrorToast } from "./components/ErrorToast";
import { Footer } from "./components/Footer";
import { StatusPanel } from "./components/StatusPanel";
import { UploadForm, type UploadSubmission } from "./components/UploadForm";

export function App() {
  const coldStart = useColdStart();
  const [phase, setPhase] = useState<UploadFlowPhase>({ kind: "idle" });
  const [fromFormat, setFromFormat] = useState<Format>("agibot");
  const [toFormat, setToFormat] = useState<Format>("lerobot-v3");

  const sessionId =
    phase.kind === "polling"
      ? phase.sessionId
      : phase.kind === "done"
        ? phase.sessionId
        : phase.kind === "failed"
          ? phase.sessionId
          : null;

  const poller = usePoller(phase.kind === "polling" ? sessionId : null);

  // Drive the phase machine off the poller's status updates.
  useEffect(() => {
    if (phase.kind !== "polling") return;
    if (!poller.status) return;
    const next: StatusResponse = poller.status;
    setPhase((current) => {
      if (current.kind !== "polling") return current;
      if (next.state === "done") {
        const url = next.download_url ?? "";
        return { kind: "done", sessionId: current.sessionId, downloadUrl: url };
      }
      if (next.state === "failed") {
        return {
          kind: "failed",
          sessionId: current.sessionId,
          error: next.error ?? {
            code: "unknown",
            message: "Conversion failed.",
            suggestion: null,
            stderr_tail: null,
          },
        };
      }
      if (next.state === "expired") {
        return {
          kind: "rejected",
          status: 410,
          detail: {
            code: "download_expired",
            message: "This run has expired and its files are gone.",
            suggestion: "Please re-upload to convert again.",
          },
        };
      }
      return { ...current, lastStatus: next };
    });
  }, [poller.status, phase.kind]);

  const onSamplePick = (sample: { fromFormat: Format; toFormat: Format }) => {
    setFromFormat(sample.fromFormat);
    setToFormat(sample.toFormat);
  };

  const onSubmit = async (submission: UploadSubmission) => {
    setPhase({ kind: "uploading" });
    setFromFormat(submission.fromFormat);
    setToFormat(submission.toFormat);
    const result = await uploadConversion({
      file: submission.file,
      fromFormat: submission.fromFormat,
      toFormat: submission.toFormat,
      maxEpisodes: submission.maxEpisodes,
    });
    if (result.kind === "accepted") {
      setPhase({
        kind: "polling",
        sessionId: result.data.session_id,
        // Seed with a synthetic pending status so the StatusPanel renders
        // immediately while the first poll is in flight.
        lastStatus: {
          session_id: result.data.session_id,
          state: "pending",
          started_at: null,
          finished_at: null,
          expires_at: result.data.expires_at,
          error: null,
          download_url: null,
          estimated_progress_pct: null,
        },
      });
    } else if (result.kind === "busy") {
      setPhase({
        kind: "rejected",
        status: 429,
        detail: result.detail,
      });
    } else {
      setPhase({ kind: "rejected", status: result.status, detail: result.detail });
    }
  };

  const showUploadForm =
    phase.kind === "idle" ||
    phase.kind === "uploading" ||
    phase.kind === "rejected";

  const status: StatusResponse | null =
    phase.kind === "polling"
      ? poller.status ?? phase.lastStatus
      : null;

  return (
    <>
      <ColdStartOverlay state={coldStart} />
      <main className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:py-12">
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">agibridge</h1>
          <p className="text-sm text-stone-700 dark:text-stone-300">
            Convert AgiBot World ↔ LeRobot v3 datasets in your browser.{" "}
            <span className="font-medium">{EPHEMERAL_NOTICE}</span>
          </p>
        </header>

        <BetaBanner visible={fromFormat === "agibot"} />

        {showUploadForm && (
          <section
            aria-labelledby="upload-section-heading"
            className="space-y-4 rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900/40"
          >
            <h2 id="upload-section-heading" className="sr-only">
              Upload form
            </h2>
            <UploadForm
              disabled={phase.kind === "uploading"}
              initialFromFormat={fromFormat}
              initialToFormat={toFormat}
              onSubmit={onSubmit}
            />
            <p className="text-xs text-stone-600 dark:text-stone-400">
              {EPHEMERAL_NOTICE}
            </p>
            {fromFormat === "agibot" && (
              <p className="text-xs text-stone-600 dark:text-stone-400">
                {MULTI_CAMERA_NOTE}
              </p>
            )}
          </section>
        )}

        {phase.kind === "rejected" && (
          <ErrorToast
            error={phase.detail}
            sessionId={null}
            onDismiss={() => setPhase({ kind: "idle" })}
          />
        )}

        {phase.kind === "polling" && status && <StatusPanel status={status} />}

        {phase.kind === "done" && (
          <StatusPanel
            status={{
              session_id: phase.sessionId,
              state: "done",
              started_at: null,
              finished_at: null,
              expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
              error: null,
              download_url: phase.downloadUrl,
              estimated_progress_pct: null,
            }}
          />
        )}

        {phase.kind === "failed" && (
          <>
            <StatusPanel
              status={{
                session_id: phase.sessionId,
                state: "failed",
                started_at: null,
                finished_at: null,
                expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
                error: phase.error,
                download_url: null,
                estimated_progress_pct: null,
              }}
            />
            <ErrorToast
              error={phase.error}
              sessionId={phase.sessionId}
              onDismiss={() => setPhase({ kind: "idle" })}
            />
          </>
        )}

        {(phase.kind === "idle" || phase.kind === "rejected") && (
          <EmptyState onPickSample={onSamplePick} />
        )}

        <p className="text-xs text-stone-600 dark:text-stone-400">
          {EPHEMERAL_NOTICE}
        </p>

        <Footer />
      </main>
    </>
  );
}
