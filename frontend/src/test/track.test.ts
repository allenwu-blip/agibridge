/**
 * `track()` helper: fire-and-forget, privacy-safe, never breaks the flow.
 *
 * Asserts:
 *  - POSTs the right body/URL to /api/v1/events
 *  - generates + persists a random anon_id in localStorage (NOT PII)
 *  - reuses the same anon_id across calls
 *  - NEVER throws (network error, fetch throwing, blocked localStorage)
 *  - sends no Authorization header / no cookies (anonymous by design)
 *
 * NOTE on the storage shim: the jsdom build wired here exposes `localStorage`
 * as a non-functional stub (getItem/setItem are `undefined`). That is
 * exactly the "blocked localStorage" environment `track()` is hardened
 * against — so we install a minimal in-memory Storage for the
 * happy-path/persistence tests, and for the resilience test we make it
 * throw, verifying `track()` degrades to an ephemeral id instead of
 * crashing. The product code's own try/catch is what makes both safe.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ANON_KEY = "agb_anon_id";

function installMemoryStorage(): Map<string, string> {
  const store = new Map<string, string>();
  const mem = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: mem,
    configurable: true,
    writable: true,
  });
  return store;
}

let store: Map<string, string>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  store = installMemoryStorage();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function spyFetchOk() {
  return vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(null, { status: 204 }));
}

/** Re-import after resetModules so the module-level ephemeral-id cache is
 * fresh per test (track.ts memoizes the fallback id at module scope). */
async function loadTrack() {
  return await import("../lib/track");
}

describe("track()", () => {
  it("POSTs kind/anon_id/meta to /api/v1/events as JSON, no auth/cookies", async () => {
    const f = spyFetchOk();
    const { track } = await loadTrack();
    track("landing_view", { ref: "tw" });
    await Promise.resolve();

    expect(f).toHaveBeenCalledTimes(1);
    const [url, init] = f.mock.calls[0]!;
    expect(String(url)).toBe("/api/v1/events");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("omit");
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    const body = JSON.parse(String(init?.body));
    expect(body.kind).toBe("landing_view");
    expect(body.meta).toEqual({ ref: "tw" });
    expect(typeof body.anon_id).toBe("string");
    expect(body.anon_id.length).toBeGreaterThan(0);
    // Only these three keys travel — no identity/PII by construction.
    expect(Object.keys(body).sort()).toEqual(["anon_id", "kind", "meta"]);
    expect(new Headers(init?.headers).get("Authorization")).toBeNull();
  });

  it("generates a random anon_id, persists it, and reuses it across calls", async () => {
    const f = spyFetchOk();
    const { track } = await loadTrack();
    expect(store.get(ANON_KEY)).toBeUndefined();

    track("landing_view");
    await Promise.resolve();
    const stored = store.get(ANON_KEY);
    expect(stored).toBeTruthy();

    track("pricing_view");
    await Promise.resolve();
    const body1 = JSON.parse(String(f.mock.calls[0]![1]?.body));
    const body2 = JSON.parse(String(f.mock.calls[1]![1]?.body));
    expect(body1.anon_id).toBe(stored);
    expect(body2.anon_id).toBe(stored); // stable per browser
    expect(stored).not.toContain("@"); // random id, not user-derived
  });

  it("getAnonId is stable and persisted", async () => {
    const { getAnonId } = await loadTrack();
    const a = getAnonId();
    const b = getAnonId();
    expect(a).toBe(b);
    expect(store.get(ANON_KEY)).toBe(a);
  });

  it("NEVER throws when fetch rejects (network down) — fire-and-forget", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    const { track } = await loadTrack();
    expect(() => track("signup_started")).not.toThrow();
    await Promise.resolve();
    await Promise.resolve();
  });

  it("NEVER throws when fetch itself throws synchronously", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      throw new Error("fetch unavailable");
    });
    const { track } = await loadTrack();
    expect(() => track("dashboard_view")).not.toThrow();
  });

  it("NEVER throws when localStorage is blocked (private mode)", async () => {
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      writable: true,
      value: {
        getItem: () => {
          throw new Error("localStorage blocked");
        },
        setItem: () => {
          throw new Error("localStorage blocked");
        },
      },
    });
    const f = spyFetchOk();
    const { track } = await loadTrack();

    expect(() => track("checkout_started")).not.toThrow();
    await Promise.resolve();
    // Degrades to an ephemeral id (still fires) instead of crashing.
    expect(f).toHaveBeenCalledTimes(1);
    const body = JSON.parse(String(f.mock.calls[0]![1]?.body));
    expect(typeof body.anon_id).toBe("string");
    expect(body.anon_id.length).toBeGreaterThan(0);
  });
});
