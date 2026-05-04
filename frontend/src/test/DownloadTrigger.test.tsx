/**
 * StatusPanel download trigger test. DoD #1.
 *
 * What we verify:
 *   - download anchor renders on state==done and points at the canonical
 *     /api/v1/download/{session_id} path (matches app/api/download.py:32-67).
 *   - download anchor is absent on non-done states.
 *   - the download anchor uses the `download` attribute so the browser saves
 *     rather than navigates.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusPanel } from "../components/StatusPanel";
import type { StatusResponse } from "../lib/types";

function status(overrides: Partial<StatusResponse>): StatusResponse {
  return {
    session_id: "01HX_FAKE_SID",
    state: "running",
    started_at: null,
    finished_at: null,
    expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    error: null,
    download_url: null,
    estimated_progress_pct: 50,
    ...overrides,
  };
}

describe("StatusPanel download trigger", () => {
  it("renders a download anchor on state==done pointing at /api/v1/download/{id}", () => {
    render(
      <StatusPanel
        status={status({ state: "done", download_url: "/api/v1/download/01HX_FAKE_SID", estimated_progress_pct: null })}
      />,
    );
    const anchor = screen.getByRole("link", { name: /Download 01HX_FAKE_SID\.zip/i }) as HTMLAnchorElement;
    expect(anchor).toBeInTheDocument();
    expect(anchor.getAttribute("href")).toBe("/api/v1/download/01HX_FAKE_SID");
    expect(anchor.hasAttribute("download")).toBe(true);
  });

  it("does not render a download anchor while state==running", () => {
    render(<StatusPanel status={status({ state: "running" })} />);
    expect(screen.queryByRole("link", { name: /Download/i })).not.toBeInTheDocument();
  });
});
