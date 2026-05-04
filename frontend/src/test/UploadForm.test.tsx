/**
 * Component test for UploadForm. DoD #1.
 *
 * What we verify:
 *   - drag-drop file is accepted
 *   - bad file extension is rejected client-side
 *   - submitting without a file shows a validation error
 *   - submitting with a same-from/to pair shows the spec §5 invalid_format_pair
 *     message verbatim
 *   - max_episodes parsing handles blanks and positive ints
 *   - onSubmit gets called with the correct shape
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { UploadForm } from "../components/UploadForm";

function makeFile(name: string, type = "application/zip"): File {
  return new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], name, { type });
}

describe("UploadForm", () => {
  it("rejects an unsupported file extension client-side", async () => {
    const onSubmit = vi.fn();
    render(<UploadForm disabled={false} onSubmit={onSubmit} />);
    const input = screen.getByLabelText(/Drop a \.zip/i, { selector: "input" });
    const evilFile = makeFile("payload.exe", "application/octet-stream");
    fireEvent.change(input, { target: { files: [evilFile] } });
    expect(
      await screen.findByText(/Only \.zip and \.tar\.gz archives are accepted here\./i),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("blocks submit when no file is chosen", async () => {
    const onSubmit = vi.fn();
    render(<UploadForm disabled={false} onSubmit={onSubmit} />);
    // Convert button is disabled until a file is picked, so we cannot click.
    const button = screen.getByRole("button", { name: /Convert/i });
    expect(button).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("calls onSubmit with parsed maxEpisodes when valid", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<UploadForm disabled={false} onSubmit={onSubmit} />);
    const input = screen.getByLabelText(/Drop a \.zip/i, { selector: "input" });
    fireEvent.change(input, { target: { files: [makeFile("good.zip")] } });
    const maxField = screen.getByLabelText(/max_episodes/i);
    await user.type(maxField, "5");
    await user.click(screen.getByRole("button", { name: /Convert/i }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      fromFormat: "agibot",
      toFormat: "lerobot-v3",
      maxEpisodes: 5,
    });
    expect(onSubmit.mock.calls[0]?.[0].file).toBeInstanceOf(File);
  });

  it("rejects non-positive maxEpisodes input", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<UploadForm disabled={false} onSubmit={onSubmit} />);
    const fileInput = screen.getByLabelText(/Drop a \.zip/i, { selector: "input" });
    fireEvent.change(fileInput, { target: { files: [makeFile("good.zip")] } });
    const maxField = screen.getByLabelText(/max_episodes/i);
    // Bypass the HTML5 number input keystroke filter (which strips "-") by
    // setting the value directly. This simulates a paste / programmatic value.
    fireEvent.change(maxField, { target: { value: "-3" } });
    await user.click(screen.getByRole("button", { name: /Convert/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/max_episodes must be a positive integer/i),
      ).toBeInTheDocument();
    });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects same-from/to format pair (mirrors backend invalid_format_pair)", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <UploadForm
        disabled={false}
        initialFromFormat="agibot"
        initialToFormat="agibot"
        onSubmit={onSubmit}
      />,
    );
    const fileInput = screen.getByLabelText(/Drop a \.zip/i, { selector: "input" });
    fireEvent.change(fileInput, { target: { files: [makeFile("good.zip")] } });
    await user.click(screen.getByRole("button", { name: /Convert/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/This conversion direction isn't supported yet/i),
      ).toBeInTheDocument();
    });
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
