# W1 content variant picks (2026-05-04)

Allen's "highest-traffic rule by surface differentiation" review of W1 content drafts. This document records the picks; the actual draft content lives in `_workspace/content-W1/` (Allen's local working tree, not in this repo per the [PUBLISH] gate that gitignored `_workspace/`).

## Twitter bio
- **Selected: Variant C** — "Robotics dataset plumbing. Maintainer of `embodied-data` + `agibridge`: AgiBot↔LeRobot v3 converter + validator. github.com/<HANDLE>"
- Reason: only stop-scroll opener; ~135 chars (most concise); names both projects; no generic UMich tail.
- Twitter's Website field is separate from bio body — `agibridge.dev` goes there (Allen manual TODO, not in bio).
- Variants A and B retained in workspace file for record; W2 cleanup will drop them.

## Landing page (Allen personal site)
- **Selected: Variant 3 hybrid** — 1-line pain hook above the "Allen Wu — building dataset plumbing for embodied AI." identity header.
- Pain hook (drafted): "AgiBot World and LeRobot v3 schemas don't line up. Most labs write a one-off bridge — and silent training breakage is the bill."
- Reason: **surface differentiation**. agibridge product surfaces (this repo's `README.md` + `landing/index.mdx`) already pain-first per A4.1 resolution shipped by Tech Writer. Allen's personal site has different audience (recruiters / academic peers) who need identity preservation. Hybrid keeps both — 5-second scroll-bouncer sees the hook, stay-scroller sees identity.
- **Highest-traffic rule has different instantiation per surface; this is correct application, not violation.**

## Discord welcome
- **Selected: Variant A (current content)** — no changes to copy.
- Reason: low W1 priority (no Discord server exists yet); current content already voice-consistent + F-1-clean.
- W2 reactivate when Discord server is created.

## Placeholder decisions
- `<DOMAIN>` = `agibridge.dev` — substituted in earlier commit (`7ec79e7` on this branch + earlier workspace edits).
- `<EMAIL>` = **SKIP**. No Contact email added anywhere. F-1 hobby framing constraint: contact channel canonicalized to GitHub Issues. An email creates reply expectations that conflict with hobby framing. Revisit only if a future SECURITY.md needs it (W3+).
- `<RELEASE_NOTES_LINK>` = **SKIP**. No v0.1.0 release exists yet; no link to point to. Revisit at W3 actual release (inline link in CHANGELOG).
- `<HANDLE>` — TBD pending W2 Twitter account decision. (Allen GitHub handle is `allenwu-blip`; Twitter handle separate.)
- `<GITHUB_SPONSORS_URL>` — TBD pending W2 GitHub Sponsors enable (not v0 priority).
- `<DISCORD_INVITE_URL>` — TBD; no Discord yet.
- `<TWITTER_URL>` — TBD with `<HANDLE>`.

## Cross-surface protection
**Do NOT** propagate workspace landing changes to `README.md` or `landing/index.mdx`. Those are agibridge *product* surfaces; pain-first arc is correct (TW shipped per A4.1); audience differentiation by surface is intentional and correct.

## Provenance
- Allen's review message: "highest-traffic rule by surface differentiation"
- Orchestrator drafted variant rankings; Allen approved Twitter C, overrode Landing to V3 hybrid (orchestrator's lean was V2 full pain-first rewrite), confirmed Discord A.
- Workspace files updated in place with `SELECTED-W1` frontmatter + variant markers.
