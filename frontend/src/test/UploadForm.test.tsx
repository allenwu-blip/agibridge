/**
 * UploadForm: drag-drop extension validation + callback contract.
 * Acceptance #5 + #12.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MAX_UPLOAD_BYTES_HINT, UploadForm } from "../components/UploadForm";

function file(name: string) {
  return new File(["x"], name, { type: "application/octet-stream" });
}

/**
 * A File with a forced byte length — `new File(["x"], …)` is only 1 byte, so
 * the oversized-upload edge can't be exercised without overriding `.size`.
 * `File.size` is a read-only getter; redefine it on the instance.
 */
function fileOfSize(name: string, size: number) {
  const f = file(name);
  Object.defineProperty(f, "size", { value: size });
  return f;
}

describe("UploadForm", () => {
  it("rejects a disallowed extension via the role=alert slot", async () => {
    render(
      <UploadForm
        disabled={false}
        onPresignAndStart={vi.fn()}
        uploadPct={null}
      />,
    );
    const input = screen.getByLabelText(
      /drop an agibot world archive/i,
    ) as HTMLInputElement;
    // fireEvent.change (not userEvent.upload): userEvent honors the input's
    // `accept` filter and silently drops .pdf, so onChange never fires. Our
    // JS allowlist is the contract under test (drag-drop bypasses `accept`
    // too), so simulate the programmatic FileList set directly.
    fireEvent.change(input, { target: { files: [file("notes.pdf")] } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Only \.zip, \.tar, \.tar\.gz/i,
    );
  });

  it("accepts .tar (AgiBot World uncompressed, §2.3 addition)", async () => {
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(
      <UploadForm
        disabled={false}
        onPresignAndStart={onStart}
        uploadPct={null}
      />,
    );
    const input = screen.getByLabelText(/drop an agibot world archive/i);
    await userEvent.upload(input, file("episodes.tar"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.submit(screen.getByRole("button", { name: /convert/i }));
    expect(onStart).toHaveBeenCalledWith(
      expect.objectContaining({ file: expect.any(File) }),
    );
  });

  it("blocks submit with no file (callback not fired)", () => {
    const onStart = vi.fn();
    render(
      <UploadForm
        disabled={false}
        onPresignAndStart={onStart}
        uploadPct={null}
      />,
    );
    fireEvent.submit(screen.getByRole("button", { name: /convert/i }));
    expect(onStart).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /choose an archive/i,
    );
  });

  it("rejects an oversized file with a readable limit message (Track 4)", () => {
    const onStart = vi.fn();
    render(
      <UploadForm
        disabled={false}
        onPresignAndStart={onStart}
        uploadPct={null}
      />,
    );
    const input = screen.getByLabelText(/drop an agibot world archive/i);
    // One byte over the Free-plan ceiling — the fail-fast guard must trip.
    fireEvent.change(input, {
      target: { files: [fileOfSize("huge.tar.gz", MAX_UPLOAD_BYTES_HINT + 1)] },
    });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/larger than the 800 MB limit/i);
    // The copy names a path forward, not just a wall.
    expect(alert).toHaveTextContent(/upgrade/i);
    // The oversized file is dropped — submit must not fire the callback.
    fireEvent.submit(screen.getByRole("button", { name: /convert/i }));
    expect(onStart).not.toHaveBeenCalled();
  });

  it("accepts a file at exactly the size limit (boundary is >, not >=)", () => {
    render(
      <UploadForm
        disabled={false}
        onPresignAndStart={vi.fn()}
        uploadPct={null}
      />,
    );
    const input = screen.getByLabelText(/drop an agibot world archive/i);
    fireEvent.change(input, {
      target: { files: [fileOfSize("atlimit.tar.gz", MAX_UPLOAD_BYTES_HINT)] },
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders an accessible progress bar while uploading", () => {
    render(
      <UploadForm
        disabled={false}
        onPresignAndStart={vi.fn()}
        uploadPct={42}
      />,
    );
    const bar = screen.getByLabelText("Upload progress");
    expect(bar).toHaveValue(42);
  });
});
