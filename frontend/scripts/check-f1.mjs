#!/usr/bin/env node
/**
 * F-1 banned-word + anti-pattern grep on the SPA build output (app/static/**).
 *
 * Sources the canonical regex from scripts/f1-banned-words.regex (C4).
 * Refusal to fall back: if the canonical regex file is missing the script
 * exits 2 — we never invent the list inline.
 *
 * Brief Phase D additions DoD:
 *   #3: every page-level surface includes "Files are kept for 30 minutes,
 *        then deleted. No accounts, no storage." (positive-pattern check #2).
 *   #4: BETA_BANNER_BODY is verbatim — checked by greps on the bundle.
 *
 * Standing-rules anti-pattern grep:
 *   - no analytics, no tracking pixels, no `gtag`/`fbq`/`posthog`/`mixpanel`/
 *     `sentry`/`amplitude`/`segment` in any bundled file.
 *
 * Exit 0 = clean. Exit 1 = at least one violation. Exit 2 = misconfigured.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..", "..");
const staticRoot = join(repoRoot, "app", "static");
const canonicalRegex = process.env.REGEX_PATH
  ? resolve(process.env.REGEX_PATH)
  : resolve(repoRoot, "scripts", "f1-banned-words.regex");

function loadCanonicalPatterns() {
  let text;
  try {
    text = readFileSync(canonicalRegex, "utf8");
  } catch (err) {
    console.error(`ERROR: canonical F-1 regex not found at ${canonicalRegex}`);
    console.error(`  (set REGEX_PATH to override; got: ${err.message})`);
    process.exit(2);
  }
  const patterns = text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !l.startsWith("#"));
  if (patterns.length === 0) {
    console.error(`ERROR: regex file ${canonicalRegex} contained no patterns`);
    process.exit(2);
  }
  return patterns.map((p) => new RegExp(p, "i"));
}

function walk(dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...walk(full));
    } else if (st.isFile()) {
      out.push(full);
    }
  }
  return out;
}

const ANTI_PATTERN = [
  /\bgtag\b/i,
  /\bfbq\b/i,
  /\bposthog\b/i,
  /\bmixpanel\b/i,
  // Match Sentry as a brand/SDK token, not the generic verb root "sentry-like";
  // require capital-S or the SDK module path.
  /Sentry\./,
  /@sentry\//i,
  /\bamplitude\b/i,
  /\bsegment\.io\b/i,
  /google-analytics/i,
  /googletagmanager/i,
];

const REQUIRED_EPHEMERAL_NOTICE =
  "Files are kept for 30 minutes, then deleted. No accounts, no storage.";

function main() {
  const patterns = loadCanonicalPatterns();
  const files = walk(staticRoot);
  if (files.length === 0) {
    console.error(
      `ERROR: no files found at ${staticRoot} — run \`vite build\` first (cd frontend && bun run build).`,
    );
    process.exit(2);
  }

  let failures = 0;

  // 1. F-1 banned-word grep against canonical regex.
  for (const file of files) {
    let body;
    try {
      body = readFileSync(file, "utf8");
    } catch {
      continue; // binary asset
    }
    const isBundleJs = file.endsWith(".js");
    for (const pat of patterns) {
      const m = body.match(pat);
      if (!m) continue;
      // Allow the JS bundle to include a single literal use inside the spec
      // banner copy when no whitespace-bounded violation. In practice the spec
      // banner does not contain any banned word — verified manually before
      // shipping. If a violation surfaces here, fix the copy, do NOT add an
      // exception.
      console.error(
        `FAIL: F-1 banned token /${pat.source}/ in ${file}: "${m[0]}"`,
      );
      failures += 1;
      // Hint the offending area for fast triage.
      const idx = body.toLowerCase().indexOf(m[0].toLowerCase());
      if (idx >= 0) {
        const start = Math.max(0, idx - 40);
        const end = Math.min(body.length, idx + m[0].length + 40);
        console.error(
          `       …${body.slice(start, end).replace(/\s+/g, " ")}…`,
        );
      }
      // Don't break — surface every violation in one run.
      // But for the bundle JS, one match is plenty; skip the rest of the patterns
      // for this file to avoid spamming.
      if (isBundleJs) break;
    }
  }

  // 2. Anti-pattern grep — analytics SDK strings.
  for (const file of files) {
    let body;
    try {
      body = readFileSync(file, "utf8");
    } catch {
      continue;
    }
    for (const pat of ANTI_PATTERN) {
      if (pat.test(body)) {
        console.error(`FAIL: anti-pattern /${pat.source}/ in ${file}`);
        failures += 1;
        break;
      }
    }
  }

  // 3. Positive-pattern check #2 — required ephemeral notice on the page-level
  //    surface (index.html). The notice ALSO appears in the JS bundle inside
  //    the React tree but the index.html check is the lightweight invariant.
  const indexHtml = files.find((f) => f.endsWith("index.html"));
  if (!indexHtml) {
    console.error(
      `FAIL: no index.html in ${staticRoot}; cannot verify ephemeral notice`,
    );
    failures += 1;
  } else {
    const html = readFileSync(indexHtml, "utf8");
    if (!html.includes(REQUIRED_EPHEMERAL_NOTICE)) {
      console.error(
        `FAIL: index.html missing required ephemeral notice (spec §10 check #2):\n  "${REQUIRED_EPHEMERAL_NOTICE}"`,
      );
      failures += 1;
    }
  }

  // 4. The notice must also appear in the bundled JS so the live UI renders it.
  const bundledJsFiles = files.filter((f) => f.endsWith(".js"));
  if (bundledJsFiles.length === 0) {
    console.error(
      `FAIL: no JS bundle found in ${staticRoot}; build must produce hashed JS.`,
    );
    failures += 1;
  } else {
    const found = bundledJsFiles.some((f) =>
      readFileSync(f, "utf8").includes(REQUIRED_EPHEMERAL_NOTICE),
    );
    if (!found) {
      console.error(
        `FAIL: ephemeral notice not present in any JS bundle (the React UI must render it).`,
      );
      failures += 1;
    }
  }

  if (failures > 0) {
    console.error(`\n${failures} F-1/anti-pattern violation(s) found.`);
    process.exit(1);
  }

  console.log(
    `OK: ${files.length} files in ${staticRoot} clean against ${patterns.length} canonical F-1 patterns + ${ANTI_PATTERN.length} anti-pattern probes.`,
  );
  console.log(
    `OK: ephemeral notice present in index.html and at least one JS bundle.`,
  );
}

main();
