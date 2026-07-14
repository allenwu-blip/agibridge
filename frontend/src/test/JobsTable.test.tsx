/**
 * JobsTable branches: skeleton (loading), empty, data, error. Plus the
 * status-badge a11y contract (color decorative, aria-label semantic).
 * Acceptance #8 + #11 + #12.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  EmptyJobs,
  JobsError,
  JobsTable,
  JobsTableSkeleton,
} from "../components/JobsTable";
import type { JobView } from "../lib/types";

vi.mock("@clerk/clerk-react", () => ({
  useAuth: () => ({ getToken: async () => "t" }),
}));

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );
}

const doneJob: JobView = {
  job_id: "11111111-1111-1111-1111-111111111111",
  state: "done",
  from_format: "agibot",
  to_format: "lerobot-v3",
  error_code: null,
  error_msg: null,
  created_at: "2026-05-16T00:00:00Z",
  started_at: "2026-05-16T00:00:01Z",
  finished_at: "2026-05-16T00:01:01Z",
};

const failedJob: JobView = {
  ...doneJob,
  job_id: "22222222-2222-2222-2222-222222222222",
  state: "failed",
  error_code: "oom_suspected",
  error_msg: "Converter ran out of memory.",
};

describe("JobsTable states", () => {
  it("skeleton renders 3 rows with table chrome", () => {
    wrap(<JobsTableSkeleton rows={3} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getAllByRole("row").length).toBe(4); // header + 3
  });

  it("empty state guides the first conversion + has no competing CTA", () => {
    render(<EmptyJobs />);
    // First-run copy names the concrete next action (Track 3).
    expect(
      screen.getByRole("heading", { name: /convert your first dataset/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/drop an agibot world archive into the upload box/i),
    ).toBeInTheDocument();
    // No button: the obvious action is the UploadForm directly above; a
    // second CTA here would only split attention.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("error state is role=alert with a Retry button", () => {
    const retry = vi.fn();
    render(<JobsError message="500" onRetry={retry} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    screen.getByRole("button", { name: /retry/i }).click();
    expect(retry).toHaveBeenCalled();
  });

  it("offline copy when message is 'Failed to fetch'", () => {
    render(<JobsError message="Failed to fetch" onRetry={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/offline/i);
  });

  it("done job exposes a Download action; status aria-label semantic", () => {
    wrap(<JobsTable jobs={[doneJob]} />);
    expect(
      screen.getByRole("button", { name: /download/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("done")).toBeInTheDocument();
  });

  it("failed job discloses error code + message on View error", async () => {
    wrap(<JobsTable jobs={[failedJob]} />);
    screen.getByRole("button", { name: /view error/i }).click();
    expect(await screen.findByText("oom_suspected")).toBeInTheDocument();
    expect(
      screen.getByText(/converter ran out of memory/i),
    ).toBeInTheDocument();
  });
});
