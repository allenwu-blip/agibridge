/**
 * WorkspaceProvisioning — the post-signup org-provisioning race recovery
 * (Track 3 #1 friction).
 *
 * Two layers, tested independently so each is fast + deterministic:
 *
 *  1. The PROBE MECHANISM (`useApiClient({ skipCache: true })`): the retry
 *     must force-mint a FRESH Clerk token so the just-written `org_id` claim
 *     is picked up. Asserted directly against the real `api.ts` with a
 *     mocked Clerk `getToken` + mocked `fetch` — see `skipCache` block.
 *
 *  2. The COMPONENT STATE MACHINE (`WorkspaceProvisioning`): mounts in the
 *     calm "Setting up your workspace…" status (NOT a scary error), calls
 *     `onReady` on the 403→success transition with no user action, and
 *     degrades to a calm manual Retry on a non-race failure. `useWorkspaceProbe`
 *     is mocked here so the 2s retry cadence does not make the test slow or
 *     timer-flaky — the probe's retry PREDICATE itself is unit-covered in
 *     queries.test.tsx.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, useApiClient } from "../lib/api";
import { NoOrganizationError, UnauthenticatedError } from "../lib/api";
import {
  PROVISION_PROBE_MAX_ATTEMPTS,
  shouldRetryProvisionProbe,
} from "../lib/queries";

// --- layer 1: the skipCache fresh-mint mechanism --------------------------

const getTokenMock = vi.fn(async () => "fake.jwt.token");
vi.mock("@clerk/clerk-react", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  getTokenMock.mockClear();
});

describe("useApiClient skipCache (provisioning-probe fresh-mint)", () => {
  it("default client mints a CACHED token (steady-state path unchanged)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, { jobs: [], next_before: null }),
    );
    const { result } = renderHook(() => useApiClient());
    await result.current.listJobs();
    // No skipCache on the normal path — only the template selector.
    expect(getTokenMock).toHaveBeenCalledWith({
      template: "agibridge",
      skipCache: false,
    });
  });

  it("skipCache client force-mints a FRESH token (picks up new org_id claim)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(200, { jobs: [], next_before: null }),
    );
    const { result } = renderHook(() => useApiClient({ skipCache: true }));
    await result.current.listJobs();
    expect(getTokenMock).toHaveBeenCalledWith({
      template: "agibridge",
      skipCache: true,
    });
  });
});

// --- layer 1b: the retry predicate (no timer needed) ----------------------

describe("shouldRetryProvisionProbe", () => {
  const noOrg = new NoOrganizationError({
    code: "no_organization",
    message: "not yet",
    suggestion: null,
  });

  it("retries a transient no_organization race until the ~30s ceiling", () => {
    expect(shouldRetryProvisionProbe(0, noOrg)).toBe(true);
    expect(
      shouldRetryProvisionProbe(PROVISION_PROBE_MAX_ATTEMPTS - 1, noOrg),
    ).toBe(true);
    // At the ceiling it gives up — the component then shows manual Retry.
    expect(
      shouldRetryProvisionProbe(PROVISION_PROBE_MAX_ATTEMPTS, noOrg),
    ).toBe(false);
  });

  it("does NOT retry a non-race failure (401 / 500 is not provisioning)", () => {
    const unauth = new UnauthenticatedError({
      code: "token_expired",
      message: "expired",
      suggestion: null,
    });
    const server = new ApiError(500, {
      code: "server_error",
      message: "boom",
      suggestion: null,
    });
    expect(shouldRetryProvisionProbe(0, unauth)).toBe(false);
    expect(shouldRetryProvisionProbe(0, server)).toBe(false);
  });
});

// --- layer 2: the component state machine ---------------------------------

// Mock the probe so the component's render branches are exercised
// deterministically (no real 2s retry cadence). Each test scripts the probe
// status/error the component should react to.
type ProbeState = {
  status: "pending" | "error" | "success";
  error: unknown;
  refetch: () => void;
};
const probeState: { current: ProbeState } = {
  current: { status: "pending", error: null, refetch: vi.fn() },
};
// Keep every real export (shouldRetryProvisionProbe, the constants) and
// override ONLY useWorkspaceProbe so the component test stays timer-free.
vi.mock("../lib/queries", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/queries")>();
  return { ...actual, useWorkspaceProbe: () => probeState.current };
});

// Import AFTER the mock is registered.
const { WorkspaceProvisioning } = await import(
  "../components/WorkspaceProvisioning"
);

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
}

describe("WorkspaceProvisioning state machine", () => {
  it("shows the calm 'setting up' status while provisioning (no scary error)", () => {
    probeState.current = {
      status: "pending",
      error: null,
      refetch: vi.fn(),
    };
    wrap(<WorkspaceProvisioning onReady={vi.fn()} />);

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/setting up your workspace/i);
    // It must NOT read like an error — no alert role.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /retry/i }),
    ).not.toBeInTheDocument();
  });

  it("calls onReady on the 403→success transition with no user action", () => {
    probeState.current = {
      status: "success",
      error: null,
      refetch: vi.fn(),
    };
    const onReady = vi.fn();
    wrap(<WorkspaceProvisioning onReady={onReady} />);
    // The success effect fires on mount — no click, no refresh.
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it("degrades to a calm manual Retry once the probe gives up", () => {
    probeState.current = {
      status: "error",
      error: new NoOrganizationError({
        code: "no_organization",
        message: "not yet",
        suggestion: null,
      }),
      refetch: vi.fn(),
    };
    const onReady = vi.fn();
    wrap(<WorkspaceProvisioning onReady={onReady} />);

    const retry = screen.getByRole("button", { name: /retry/i });
    expect(retry).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      /still setting things up/i,
    );
    retry.click();
    expect(probeState.current.refetch).toHaveBeenCalled();
    expect(onReady).not.toHaveBeenCalled();
  });
});
