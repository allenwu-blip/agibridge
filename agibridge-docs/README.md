# agibridge-docs

Mintlify scaffold for the agibridge documentation site. Built per Cycle AK (Wave 4 parallel).

## Status

**Pre-launch scaffold.** Ships at `docs.agibridge.com` (or `agibridge.com/docs`) when the deploy wave completes. Page stubs are pre-filled with content adapted from Cycle X (IA + voice), Cycle J (IA blueprint), Cycle Y (migration blog content), Cycle AA (SDK specs), and Cycle AD (FAQ).

## Important context — Docusaurus vs Mintlify

**This Mintlify scaffold overrides DR-018 default #4.**

- `DECISIONS.md` DR-018 default #4 = Docusaurus + `docusaurus-plugin-openapi-docs`, OSS self-host on Vercel, $0 incremental cost.
- Allen's Wave 4 brief explicitly chose Mintlify for the Cycle AK scaffold.
- Mintlify Pro is **$250/mo** for the polish + hosted-tier experience ([mintlify.com/pricing](https://mintlify.com/pricing) accessed 2026-05-14).

**The two options remain viable. Allen picks at deploy time:**

| Path | Cost | Setup time | Polish | Notes |
|---|---|---|---|---|
| **Mintlify** (this scaffold) | $250/mo | ~10 min (`mintlify deploy`) | High out-of-box | Hosted, polished, AI assistant, preview deploys |
| **Docusaurus** (DR-018 default) | $0 | ~4-6 hr | Tunable, requires theme work | OSS, self-host on Vercel, full control |

The Cycle X `docs-structure.md` is the Docusaurus alternative; the structure (Quickstart, Guides/Concepts, API Reference, Pricing, FAQ, Changelog) is equivalent and portable across both platforms. Migrating between them is ~1 day work if Allen flips later.

**Cost framing**: $250/mo = $3,000/yr = 12% of the realistic 60-day MRR ceiling ($500-2,500 MRR per `_day1_research/competitive_landscape.md` §6). Whether this is acceptable depends on whether Allen treats docs spend as an opex-ratio line or a strategic capex.

**Revisit trigger if Mintlify chosen**: if Allen-time savings drop below ~2 hr/mo vs Docusaurus, consider migration.

[DECISION NEEDED] Docusaurus vs Mintlify at deploy time. This scaffold lets Allen ship Mintlify on day 1; the Docusaurus path remains in `marketing/site-copy/docs-structure.md` if cost-framing flips.

## Local development

### Prerequisites

- Node.js ≥18 LTS (Mintlify CLI requirement)
- A Mintlify account (free for individual dev; Pro for production deploy)

### Install Mintlify CLI

```bash
npm install -g mintlify
```

### Run locally

```bash
cd /Users/allenwu/embodied-data-hosted/agibridge-saas-bootstrap/agibridge-docs
mintlify dev
```

This starts a local dev server at `http://localhost:3000` with hot-reload on `.mdx` changes.

### Validate config

```bash
mintlify install     # one-time: validates mint.json schema
mintlify broken-links # check for broken internal/external links
```

## File structure

```
agibridge-docs/
├── README.md                      (this file)
├── mint.json                      (navigation + theming config)
├── images/                        (logos, favicons — TODO: design assets)
├── introduction.mdx               (landing page)
├── quickstart.mdx                 (5-step path)
├── first-conversion.mdx           (annotated walkthrough)
├── concepts/
│   ├── data-formats.mdx           (AgiBot ↔ LeRobot v3 framing)
│   ├── conversion-model.mdx       (pipeline mental model)
│   ├── r3-isolation.mdx           (security invariant)
│   └── pricing.mdx                (tier framing + self-host)
├── api-reference/
│   ├── presign-upload.mdx         (POST /api/v1/jobs/presign-upload)
│   ├── create-job.mdx             (POST /api/v1/jobs)
│   ├── list-jobs.mdx              (GET /api/v1/jobs)
│   ├── get-job.mdx                (GET /api/v1/jobs/{job_id})
│   └── presign-download.mdx       (GET /api/v1/jobs/{job_id}/presign-download)
├── migrations/
│   ├── lerobot-v2-to-v3.mdx       (companion to blog 01)
│   └── agibot-to-lerobot.mdx      (companion to blog 05)
├── sdks/
│   ├── python.mdx                 (D5+ Python SDK contract)
│   └── typescript.mdx             (D5+ TS SDK contract)
├── faq.mdx                        (inherited from Cycle AD)
└── changelog.mdx                  (v0.1.0 placeholder)
```

## Navigation tree (from mint.json)

```
agibridge docs
├── Getting Started
│   ├── Introduction
│   ├── Quickstart
│   └── Your first conversion
├── Concepts
│   ├── Data formats
│   ├── Conversion model
│   ├── R3: cross-organization isolation
│   └── Pricing model
├── API Reference
│   ├── Presign upload
│   ├── Create job
│   ├── List jobs
│   ├── Get job
│   └── Presign download
├── Migrations
│   ├── LeRobot v2 → v3 migration
│   └── AgiBot World → LeRobot v3
├── SDKs
│   ├── Python SDK
│   └── TypeScript SDK
└── Reference
    ├── FAQ
    └── Changelog
```

Top-bar links: Pricing, GitHub. Top-bar CTA: "Start free preview".
Anchors: API Reference, SDKs, PyPI: embodied-data.

## Voice anchor

This scaffold inherits the Cycle X 7-point voice profile (per `marketing/site-copy/homepage.md` §"Voice anchor recommendation"):

1. Lead with format names, not abstractions ("AgiBot World ↔ LeRobot v3" beats "robot dataset conversion")
2. Cite GH issues / arxiv / source URLs inline
3. No marketing exclamation, no "we're so excited", no emojis (unless Allen opts in)
4. Acknowledge limits honestly ("no GPU yet", "no API keys at MVP")
5. Pricing is published — never use "contact sales" framing for Solo / Team
6. Bidirectional, not unidirectional — `AgiBot World ↔ LeRobot v3` with the arrow
7. OSS lib is named and linked, not hidden

`[VOICE-CHECK]` markers appear inline in MDX files that lean marketing-adjacent (`introduction.mdx`, `quickstart.mdx`, `concepts/pricing.mdx`). Count: 3 active markers across the scaffold. These are author-time review pings, not customer-facing — Mintlify renders MDX comments (`{/* ... */}`) as zero-output.

## What still needs to land before launch

- [ ] Design assets — logo light/dark SVG, favicon (placeholder paths in `mint.json` reference `/images/logo-light.svg`, `/images/logo-dark.svg`, `/images/favicon.svg`)
- [ ] Domain — `docs.agibridge.com` CNAME (pending DR-016 domain ratify)
- [ ] OpenAPI spec import — Mintlify can pull from `https://api.agibridge.com/openapi.json` once backend deploys; replace hand-written `api-reference/*.mdx` with Mintlify's OpenAPI auto-rendering if preferred (or keep hand-written for narrative voice — your call)
- [ ] Algolia / Mintlify search index population (auto on first deploy)
- [ ] [VOICE-CHECK] resolution on `quickstart.mdx` 5-min claim (benchmark on prod before publishing the 5-min number)
- [ ] v0.1.0 changelog entry filled in with actual ship date + tag

## Deploy

```bash
# When ready:
mintlify deploy
```

Mintlify hosts the site at `<your-subdomain>.mintlify.app` by default. Custom domain (`docs.agibridge.com`) configured in the Mintlify dashboard once DR-016 lands.

## Migration to Docusaurus if cost-framing flips

If Allen decides $250/mo is too high vs $0 OSS-self-host:

1. Run `npx create-docusaurus@latest docs-site classic --typescript`
2. Install `docusaurus-plugin-openapi-docs` + `docusaurus-theme-openapi-docs`
3. Port each `.mdx` file in this directory — Mintlify and Docusaurus MDX are 95% compatible (mainly: `<CardGroup>`, `<Card>`, `<AccordionGroup>`, `<Accordion>` components need MDX equivalents in Docusaurus theme)
4. Reuse the same navigation tree from `mint.json` → `sidebars.js`
5. Deploy to existing Vercel project alongside marketing site

Estimated migration time: ~1 day.

## Sources

- IA + voice anchor — `marketing/site-copy/docs-structure.md` (Cycle X)
- IA blueprint + tooling decision context — `docs/structure.md` (Cycle J)
- API endpoint spec — `dispatches/D4_specs.md` §3 amendment #3 (5 MVP endpoints)
- FAQ inheritance — `support/faq.md` (Cycle AD)
- SDK contracts — `sdk/python-spec.md` + `sdk/typescript-spec.md` (Cycle AA)
- Migration blog content — `marketing/blog/01-lerobot-v2-v3-migration.md`, `marketing/blog/05-agibot-1m-trajectory-onboarding.md` (Cycle Y)
- Mintlify quickstart — [mintlify.com/docs/quickstart](https://mintlify.com/docs/quickstart) (accessed 2026-05-14)
- Mintlify pricing — [mintlify.com/pricing](https://mintlify.com/pricing) (accessed 2026-05-14)
- DR-018 default #4 = Docusaurus — `/Users/allenwu/.plans/agibridge-2026/DECISIONS.md`
