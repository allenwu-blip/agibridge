/**
 * ProgressBar test. DoD #8 — clamp to 99 while running/validating, never 100
 * before state==done; "(estimate)" annotation visible.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProgressBar } from "../components/ProgressBar";

describe("ProgressBar", () => {
  it("shows '(estimate)' annotation while running", () => {
    render(<ProgressBar state="running" estimatedProgressPct={42} />);
    expect(screen.getByText(/42%/)).toBeInTheDocument();
    expect(screen.getByText(/\(estimate\)/i)).toBeInTheDocument();
  });

  it("clamps to 99 even if backend slips past while still running", () => {
    render(<ProgressBar state="running" estimatedProgressPct={250} />);
    const node = screen.getByRole("progressbar");
    expect(node).toHaveAttribute("aria-valuenow", "99");
    expect(screen.getByText(/99%/)).toBeInTheDocument();
  });

  it("renders 100 only when state==done", () => {
    render(<ProgressBar state="done" estimatedProgressPct={null} />);
    const node = screen.getByRole("progressbar");
    expect(node).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText(/\(complete\)/i)).toBeInTheDocument();
  });

  it("never reads 100 with state==validating", () => {
    render(<ProgressBar state="validating" estimatedProgressPct={99} />);
    const node = screen.getByRole("progressbar");
    expect(node).toHaveAttribute("aria-valuenow", "99");
    expect(screen.queryByText(/100%/)).not.toBeInTheDocument();
  });

  it("does NOT round to 100 on failed/expired", () => {
    render(<ProgressBar state="failed" estimatedProgressPct={88} />);
    const node = screen.getByRole("progressbar");
    expect(node).toHaveAttribute("aria-valuenow", "88");
  });
});
