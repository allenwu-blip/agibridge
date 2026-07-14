/**
 * useApiClient: assert Bearer injection from a mocked Clerk getToken, and
 * that a 401 throws UnauthenticatedError, a 403 no_organization throws
 * NoOrganizationError. Acceptance #12 + #3.
 */

import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  NoOrganizationError,
  UnauthenticatedError,
  useApiClient,
} from "../lib/api";

const getTokenMock = vi.fn(async () => "fake.jwt.token");

vi.mock("@clerk/clerk-react", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

afterEach(() => {
  vi.restoreAllMocks();
  getTokenMock.mockClear();
});

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("useApiClient", () => {
  it("injects Authorization: Bearer <token> from Clerk getToken", async () => {
    const f = mockFetch(200, { jobs: [], next_before: null });
    const { result } = renderHook(() => useApiClient());

    await result.current.listJobs();

    expect(getTokenMock).toHaveBeenCalled();
    const init = f.mock.calls[0]![1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer fake.jwt.token");
    expect(init.credentials).toBe("omit");
  });

  it("throws UnauthenticatedError on 401 (flat middleware envelope)", async () => {
    mockFetch(401, {
      code: "missing_authorization",
      message: "Authorization header required.",
      suggestion: null,
    });
    const { result } = renderHook(() => useApiClient());
    await expect(result.current.listJobs()).rejects.toBeInstanceOf(
      UnauthenticatedError,
    );
  });

  it("throws NoOrganizationError on 403 no_organization", async () => {
    mockFetch(403, {
      code: "no_organization",
      message: "Your account is not part of an organization yet.",
      suggestion: null,
    });
    const { result } = renderHook(() => useApiClient());
    await expect(result.current.listJobs()).rejects.toBeInstanceOf(
      NoOrganizationError,
    );
  });

  it("forwards size_bytes in the presign-upload body (Track 4 size gate)", async () => {
    const f = mockFetch(201, {
      job_id: "j",
      upload_url: "u",
      key: "k",
      expires_in: 900,
    });
    const { result } = renderHook(() => useApiClient());

    await result.current.presignUpload({
      filename: "x.tar.gz",
      from_format: "agibot",
      to_format: "lerobot-v3",
      size_bytes: 123_456,
    });

    const init = f.mock.calls[0]![1] as RequestInit;
    const body = JSON.parse(init.body as string);
    // The backend reads size_bytes to reject an oversized file with a clean
    // 413 before issuing the R2 URL (app/api/jobs.py presign_upload).
    expect(body.size_bytes).toBe(123_456);
  });

  it("throws ApiError carrying the {detail:{...}} route envelope on 400", async () => {
    mockFetch(400, {
      detail: {
        code: "invalid_format_pair",
        message: "This conversion direction isn't supported.",
        suggestion: "Try AgiBot -> LeRobot v3, or the reverse.",
      },
    });
    const { result } = renderHook(() => useApiClient());
    await expect(
      result.current.presignUpload({
        filename: "x.tar",
        from_format: "agibot",
        to_format: "agibot",
      }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 400,
      detail: { code: "invalid_format_pair" },
    });
    expect(ApiError).toBeTypeOf("function");
  });
});
