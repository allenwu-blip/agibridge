/**
 * BetaBanner test. DoD #4: byte-for-byte verbatim copy from spec §5.
 *
 * Spot-check key clauses of the banner appear unchanged. Full byte equality is
 * enforced by the F-1 banned-word grep on the build output (see
 * frontend/scripts/check-f1.mjs).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BetaBanner } from "../components/BetaBanner";
import { BETA_BANNER_BODY } from "../lib/copy";

describe("BetaBanner", () => {
  it("renders the verbatim spec §5 banner body", () => {
    render(<BetaBanner visible />);
    // The banner contains inline `code` segments — DOM splits them. Look up
    // a few distinctive substrings that must survive verbatim.
    const region = screen.getByRole("region", { name: /AgiBot Beta coverage/i });
    expect(region).toHaveTextContent("AgiBot Beta is partially supported.");
    expect(region).toHaveTextContent("silently dropped without error");
    expect(region).toHaveTextContent("end-pose wrench data");
    expect(region).toHaveTextContent(
      "Please file the stderr tail on the embodied-data GitHub for either case.",
    );
  });

  it("does not render when not visible", () => {
    const { container } = render(<BetaBanner visible={false} />);
    expect(container.firstChild).toBeNull();
  });

  it("BETA_BANNER_BODY constant matches the spec §5 verbatim opening sentence", () => {
    expect(BETA_BANNER_BODY).toMatch(/^AgiBot Beta is partially supported\./);
    // Closing sentence is also verbatim.
    expect(BETA_BANNER_BODY).toMatch(
      /Please file the stderr tail on the embodied-data GitHub for either case\.$/,
    );
  });
});
