/**
 * "Try it, no signup" demo on Landing (`Landing.tsx` TryDemo +
 * `lib/demo.ts`).
 *
 * Asserts the funnel-critical states without ever hitting the network:
 *  - idle CTA renders, click -> running state
 *  - success -> REAL-shaped result (validator + dataset) + sign-up CTA
 *  - soft failure (ok:false) -> readable copy + "Try again", NEVER a crash
 *  - 503 busy -> calm "busy" copy (not a raw error / blank)
 *  - `demo_started` client INTENT is tracked on click (server emits
 *    `demo_run`; the client must NOT be able to forge a conversion)
 *
 * Clerk + react-router are stubbed (this is a unit test of the demo
 * surface, not an auth/route integration test) — same isolation posture
 * the other component tests use.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Stub Clerk: SignedOut renders children (anonymous visitor), SignedIn does
// not. The demo surface itself is auth-agnostic.
vi.mock("@clerk/clerk-react", () => ({
  SignedOut: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SignedIn: () => null,
}));

// Stub react-router <Link> as a plain anchor so onClick (the track() call)
// still fires.
vi.mock("react-router-dom", () => ({
  Link: ({
    children,
    onClick,
    to,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    to: string;
  }) => (
    <a href={to} onClick={onClick}>
      {children}
    </a>
  ),
}));

const trackMock = vi.fn();
vi.mock("../lib/track", () => ({
  track: (...a: unknown[]) => trackMock(...a),
  getAnonId: () => "anon-test-123",
}));

import { Landing } from "../pages/Landing";

const SUCCESS = {
  ok: true,
  state: "done",
  from_format: "agibot",
  to_format: "lerobot-v3",
  sample: "AgiBot World Beta task 675 (1 episode, ~1.5 MB)",
  validator_result: "PASS",
  checks: [
    { name: "schema conformance", status: "PASS", detail: "v3.0 ok" },
    { name: "fps consistency", status: "SKIP", detail: "no videos" },
    {
      name: "timestamp monotonicity",
      status: "PASS",
      detail: "1090 rows mono",
    },
  ],
  dataset: {
    codebase_version: "v3.0",
    episodes: 1,
    frames: 1090,
    tasks: 1,
    fps: 30,
    output_bytes: 65536,
    file_tree: ["meta/info.json", "data/chunk-000/file-000.parquet"],
  },
  message: "Real conversion complete — validated LeRobot v3 output.",
};

function mockFetch(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
}

beforeEach(() => {
  trackMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TryDemo (no-signup demo on Landing)", () => {
  it("renders the idle CTA and tracks demo_started on click", async () => {
    mockFetch(200, SUCCESS);
    render(<Landing />);

    const btn = screen.getByRole("button", {
      name: /run the demo conversion/i,
    });
    fireEvent.click(btn);

    // Client INTENT event fired (server emits the trustworthy demo_run).
    expect(trackMock).toHaveBeenCalledWith("demo_started", {
      surface: "landing",
    });
    // Immediately shows the running state (real-infra honesty copy).
    expect(
      screen.getByRole("button", { name: /converting on real infra/i }),
    ).toBeInTheDocument();
  });

  it("shows the REAL validator + dataset summary and a sign-up CTA on success", async () => {
    mockFetch(200, SUCCESS);
    render(<Landing />);
    fireEvent.click(
      screen.getByRole("button", { name: /run the demo conversion/i }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/real conversion complete/i),
      ).toBeInTheDocument(),
    );
    // Real dataset shape surfaced (1 episode / 1090 frames / v3.0). The
    // number sits in its own <strong>, label in a sibling text node, so
    // assert the parent's normalized textContent.
    expect(
      screen.getByText(
        (_t, el) =>
          el?.tagName === "P" &&
          Boolean(el.textContent) &&
          /1090\s*frames/i.test(el.textContent as string) &&
          /LeRobot v3/i.test(el.textContent as string),
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/Validator: PASS/i)).toBeInTheDocument();
    expect(screen.getByText(/schema conformance/i)).toBeInTheDocument();
    // Soft conversion CTA to sign up with their own data.
    const cta = screen.getByRole("link", {
      name: /now convert your own data/i,
    });
    fireEvent.click(cta);
    expect(trackMock).toHaveBeenCalledWith("signup_started", { cta: "demo" });
  });

  it("degrades to readable copy on a soft failure (ok:false), never a crash", async () => {
    mockFetch(200, {
      ok: false,
      state: "error",
      message: "The demo hit an unexpected snag.",
      suggestion: "This is just our demo box being busy.",
    });
    render(<Landing />);
    fireEvent.click(
      screen.getByRole("button", { name: /run the demo conversion/i }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/the demo hit an unexpected snag/i),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/just our demo box being busy/i),
    ).toBeInTheDocument();
    // Recovery affordance, not a dead end.
    expect(
      screen.getByRole("button", { name: /try again/i }),
    ).toBeInTheDocument();
  });

  it("shows calm 'busy' copy on a 503 (single-flight back-pressure)", async () => {
    mockFetch(503, {
      ok: false,
      state: "busy",
      message: "The demo box is converting another sample right now.",
      suggestion: "Give it a few seconds and try again.",
    });
    render(<Landing />);
    fireEvent.click(
      screen.getByRole("button", { name: /run the demo conversion/i }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/converting another sample right now/i),
      ).toBeInTheDocument(),
    );
    // No raw status code / blank — readable, with a retry.
    expect(
      screen.getByRole("button", { name: /try again/i }),
    ).toBeInTheDocument();
  });

  it("maps a 429 rate-limit to readable busy copy (never throws)", async () => {
    mockFetch(429, { detail: { code: "rate_limit_exceeded" } });
    render(<Landing />);
    fireEvent.click(
      screen.getByRole("button", { name: /run the demo conversion/i }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/lots of people are trying the demo/i),
      ).toBeInTheDocument(),
    );
  });

  it("never throws when the network is down (fetch rejects)", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("net down"));
    render(<Landing />);
    expect(() =>
      fireEvent.click(
        screen.getByRole("button", { name: /run the demo conversion/i }),
      ),
    ).not.toThrow();
    await waitFor(() =>
      expect(
        screen.getByText(/couldn't reach the demo just now/i),
      ).toBeInTheDocument(),
    );
  });
});
