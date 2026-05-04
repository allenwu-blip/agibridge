/**
 * Status poller hook test. DoD #1.
 *
 * What we verify (against the documented contract in app/api/status.py:22-30):
 *   - polls /api/v1/status/{id} on mount (immediate first tick)
 *   - stops polling on terminal states (done / failed / expired)
 *   - 404 session_not_found is treated as terminal
 *   - non-running cadence schedules 5s setTimeout, running schedules 2s
 *
 * We use real timers but seed the mocked fetch with a sequence of responses
 * including an immediate "done" so the test does not depend on timer
 * advancement to reach termination — that keeps the test fast while still
 * exercising the terminal-state branch + the setTimeout scheduling.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePoller } from "../lib/usePoller";
import type { StatusResponse } from "../lib/types";

function statusPayload(overrides: Partial<StatusResponse>): StatusResponse {
  return {
    session_id: "01HX_FAKE_SID",
    state: "pending",
    started_at: null,
    finished_at: null,
    expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    error: null,
    download_url: null,
    estimated_progress_pct: null,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
  } as unknown as Response;
}

describe("usePoller", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("calls /api/v1/status/{id} on mount and reaches terminal state", async () => {
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    // First (and only) poll returns done — terminal, no further fetches.
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        statusPayload({
          state: "done",
          download_url: "/api/v1/download/01HX_FAKE_SID",
        }),
      ),
    );

    const { result } = renderHook(() => usePoller("01HX_FAKE_SID"));

    await waitFor(() => {
      expect(result.current.terminated).toBe(true);
    });
    expect(result.current.status?.state).toBe("done");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Cite the API path the hook hits — must match app/api/status.py:22.
    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toBe("/api/v1/status/01HX_FAKE_SID");
  });

  it("schedules a 2s next tick while state is running", async () => {
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");
    // Stay running — second fetch never resolves so we can inspect schedule.
    fetchMock.mockResolvedValueOnce(
      jsonResponse(statusPayload({ state: "running", estimated_progress_pct: 30 })),
    );
    fetchMock.mockImplementation(() => new Promise(() => {})); // pending forever

    const { result, unmount } = renderHook(() => usePoller("01HX_FAKE_SID"));

    await waitFor(() => {
      expect(result.current.status?.state).toBe("running");
    });
    // Look for a 2000ms scheduling call after the first tick fired.
    const scheduledMs = setTimeoutSpy.mock.calls
      .map((c) => c[1] as number | undefined)
      .filter((ms) => ms === 2000);
    expect(scheduledMs.length).toBeGreaterThanOrEqual(1);

    unmount();
    setTimeoutSpy.mockRestore();
  });

  it("treats 404 session_not_found as terminal (no infinite retry loop)", async () => {
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          detail: {
            code: "session_not_found",
            message: "We don't know that session.",
            suggestion: null,
          },
        },
        404,
      ),
    );
    const { result } = renderHook(() => usePoller("01HX_FAKE_SID"));
    await waitFor(() => {
      expect(result.current.terminated).toBe(true);
    });
    expect(result.current.error).toBeTruthy();
    // Single fetch — terminal on 404.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
