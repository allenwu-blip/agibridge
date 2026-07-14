/**
 * Track 4 edge-hardening — Stripe checkout-return banner.
 *
 * `api.ts` sends Stripe a `cancel_url` of `/dashboard?checkout=cancel`, but
 * the Dashboard previously never read that param: a cancelled checkout
 * landed the customer on a silent dashboard with no acknowledgment. These
 * tests pin the `CheckoutBanner` recovery: calm copy on cancel, an explicit
 * "no charge", a success acknowledgment, and the param stripped from the URL
 * so a refresh does not resurface a stale banner.
 *
 * `CheckoutBanner` depends only on `window.location` + `history` — no Clerk
 * / TanStack hooks — so it is rendered in isolation (no provider wrapper).
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CheckoutBanner } from "../pages/Dashboard";

function setUrl(search: string) {
  window.history.replaceState({}, "", `/dashboard${search}`);
}

describe("CheckoutBanner", () => {
  beforeEach(() => setUrl(""));
  afterEach(() => setUrl(""));

  it("renders nothing when there is no ?checkout param", () => {
    setUrl("");
    const { container } = render(<CheckoutBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for an unrecognized ?checkout value", () => {
    setUrl("?checkout=bogus");
    const { container } = render(<CheckoutBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a calm, no-charge message on ?checkout=cancel", () => {
    setUrl("?checkout=cancel");
    render(<CheckoutBanner />);
    const banner = screen.getByRole("status");
    // Explicit reassurance — the #1 anxiety after exiting a billing flow.
    expect(banner).toHaveTextContent(/no charge was made/i);
    expect(banner).toHaveTextContent(/cancelled/i);
  });

  it("shows an activation acknowledgment on ?checkout=success", () => {
    setUrl("?checkout=success");
    render(<CheckoutBanner />);
    expect(screen.getByRole("status")).toHaveTextContent(/activated/i);
  });

  it("strips ?checkout from the URL so a refresh does not re-show it", () => {
    setUrl("?checkout=cancel");
    render(<CheckoutBanner />);
    expect(window.location.search).not.toContain("checkout");
  });

  it("preserves other query params when stripping ?checkout", () => {
    setUrl("?checkout=cancel&ref=email");
    render(<CheckoutBanner />);
    expect(window.location.search).toContain("ref=email");
    expect(window.location.search).not.toContain("checkout");
  });

  it("can be dismissed by the user", async () => {
    setUrl("?checkout=cancel");
    render(<CheckoutBanner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
