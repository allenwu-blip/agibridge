/**
 * ErrorToast voice-register test. DoD #9 / C5 propagation.
 *
 * Verifies that the three voice-guide.md Surface 2 codes render the verbatim
 * prefix strings and the GitHub issues link. The verbatim strings live in
 * lib/copy.ts and are sourced from agibridge/landing/voice-guide.md lines 51,
 * 54, 57.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ErrorToast } from "../components/ErrorToast";
import {
  TOAST_CONVERTER_REJECTED_INPUT_PREFIX,
  TOAST_GH_ISSUES_URL,
  TOAST_OOM_PREFIX,
  TOAST_TIMEOUT_PREFIX,
} from "../lib/copy";

describe("ErrorToast voice-guide Surface 2", () => {
  it("renders the verbatim converter_rejected_input prefix", () => {
    render(
      <ErrorToast
        error={{
          code: "converter_rejected_input",
          message: "FileNotFoundError: meta/info.json",
          suggestion: "Re-export from the source dataset.",
        }}
        sessionId="01HX_SID"
      />,
    );
    expect(screen.getByText(TOAST_CONVERTER_REJECTED_INPUT_PREFIX)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /embodied-data GitHub/i }) as HTMLAnchorElement;
    expect(link.href).toBe(TOAST_GH_ISSUES_URL);
  });

  it("renders the verbatim oom_suspected prefix", () => {
    render(
      <ErrorToast
        error={{
          code: "oom_suspected",
          message: "killed by SIGKILL",
          suggestion: null,
        }}
        sessionId={null}
      />,
    );
    expect(screen.getByText(TOAST_OOM_PREFIX)).toBeInTheDocument();
  });

  it("renders the verbatim conversion_timeout prefix", () => {
    render(
      <ErrorToast
        error={{
          code: "conversion_timeout",
          message: "wallclock 25min",
          suggestion: null,
        }}
        sessionId={null}
      />,
    );
    expect(screen.getByText(TOAST_TIMEOUT_PREFIX)).toBeInTheDocument();
  });

  it("falls back to spec §5 message for non-Surface-2 codes", () => {
    render(
      <ErrorToast
        error={{
          code: "archive_too_large",
          message: "The archive is larger than the 1.5 GB limit on this hosted environment.",
          suggestion: "For larger datasets, run the CLI locally: `pip install embodied-data`.",
        }}
        sessionId={null}
      />,
    );
    expect(
      screen.getByText(/larger than the 1\.5 GB limit on this hosted environment/i),
    ).toBeInTheDocument();
  });
});
