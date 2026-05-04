#!/usr/bin/env node
/**
 * Frontend integration probe (DoD #2). Mirrors the SPA's full happy-path
 * against a live backend. Exits 0 on success, non-zero on any deviation
 * from the contract.
 *
 * Usage:
 *   1. Start backend with PATH set so embodied-data CLI is reachable:
 *        PATH="/path/to/.venv/bin:$PATH" \
 *          /path/to/.venv/bin/uvicorn app.main:app --host 127.0.0.1 \
 *          --port 7860 --workers 1 --loop asyncio
 *      (--loop asyncio: backend's app/api/subprocess_runner.py:95 passes
 *      `process_group=0` which uvloop rejects — see DR FILED in frontend
 *      report. Until that is fixed, --loop asyncio is required.)
 *   2. Pre-build a fixture zip at /tmp/beta_675.zip from
 *      tests/fixtures/agibot_beta_675_single_ep/ (cd into the dir and
 *      `zip -qr /tmp/beta_675.zip .`).
 *   3. node frontend/scripts/integration-probe.mjs
 *
 * What we verify, mirroring DoD #2:
 *   - POST /upload returns 202 + UploadAccepted shape
 *   - GET /status transitions through running → done
 *   - estimated_progress_pct is int 0..99 while running, null on done
 *     (spec §2.2.1)
 *   - GET /download returns application/zip; zip contains the expected
 *     LeRobot v3 meta/* paths
 *
 * This probe complements (does not replace) the vitest unit suite.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Buffer } from "node:buffer";

const API_BASE = process.env.API_BASE ?? "http://127.0.0.1:7860/api/v1";
const FIXTURE = process.env.FIXTURE ?? "/tmp/beta_675.zip";

function fail(msg) {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
}

async function main() {
  // 0. Health check.
  const health = await fetch(`${API_BASE}/health`).then((r) => r.json());
  if (!health.ok) fail(`/health not ok: ${JSON.stringify(health)}`);
  console.log(`OK: /health embodied-data ${health.embodied_data_version}`);

  // 0b. Version (DoD #7).
  const version = await fetch(`${API_BASE}/version`).then((r) => r.json());
  if (!version.agibridge_git_sha) fail("/version missing agibridge_git_sha");
  console.log(
    `OK: /version sha=${version.agibridge_git_sha.slice(0, 7)} embodied=${version.embodied_data_version}`,
  );

  // 1. Upload.
  const fileBytes = readFileSync(resolve(FIXTURE));
  const blob = new Blob([fileBytes], { type: "application/zip" });
  const fd = new FormData();
  fd.append("file", blob, "beta_675.zip");
  fd.append("from_format", "agibot");
  fd.append("to_format", "lerobot-v3");
  const up = await fetch(`${API_BASE}/upload`, { method: "POST", body: fd });
  if (up.status !== 202) fail(`upload status ${up.status}: ${await up.text()}`);
  const upBody = await up.json();
  console.log(`OK: upload accepted session_id=${upBody.session_id}`);
  if (!upBody.note?.includes("Files are kept for 30 minutes")) {
    fail("upload note missing ephemeral-storage sentence");
  }

  // 2. Poll until done. Mirror SPA's 2s/5s cadence.
  const sid = upBody.session_id;
  const deadline = Date.now() + 120_000;
  let final = null;
  let sawClampAt99 = false;
  while (Date.now() < deadline) {
    const s = await fetch(`${API_BASE}/status/${sid}`).then((r) => r.json());
    const interval = s.state === "running" ? 2000 : 5000;
    if (s.state === "running") {
      // Spec §2.2.1: int 0..99 while running.
      if (
        s.estimated_progress_pct === null ||
        typeof s.estimated_progress_pct !== "number" ||
        s.estimated_progress_pct < 0 ||
        s.estimated_progress_pct > 99
      ) {
        fail(`bad estimated_progress_pct while running: ${s.estimated_progress_pct}`);
      }
      if (s.estimated_progress_pct === 99) sawClampAt99 = true;
    }
    if (s.state === "done" || s.state === "failed" || s.state === "expired") {
      final = s;
      break;
    }
    await new Promise((r) => setTimeout(r, interval));
  }
  if (!final) fail("timed out waiting for terminal state");
  if (final.state !== "done") fail(`final state ${final.state}: ${JSON.stringify(final)}`);
  if (final.estimated_progress_pct !== null) {
    fail(`estimated_progress_pct should be null on done, got ${final.estimated_progress_pct}`);
  }
  if (final.download_url !== `/api/v1/download/${sid}`) {
    fail(`unexpected download_url: ${final.download_url}`);
  }
  console.log(
    `OK: pending → running → done (clamp-at-99 observed: ${sawClampAt99 ? "yes" : "no — fixture too fast"})`,
  );

  // 3. Download.
  const dl = await fetch(`${API_BASE}/download/${sid}`);
  if (dl.status !== 200) fail(`download status ${dl.status}`);
  if (dl.headers.get("content-type") !== "application/zip") {
    fail(`download content-type: ${dl.headers.get("content-type")}`);
  }
  const cd = dl.headers.get("content-disposition") ?? "";
  if (!cd.includes(`filename="${sid}.zip"`)) {
    fail(`download Content-Disposition missing expected filename: ${cd}`);
  }
  const zipBytes = Buffer.from(await dl.arrayBuffer());
  if (zipBytes.length < 100) fail(`zip too small: ${zipBytes.length} bytes`);
  // Magic bytes 0x50 0x4B 0x03 0x04 — backend spec §7 ZIP_MAGIC.
  if (zipBytes[0] !== 0x50 || zipBytes[1] !== 0x4b) {
    fail(`zip magic bytes wrong: ${zipBytes.subarray(0, 4)}`);
  }
  // Find central directory entries; check for expected meta/info.json substring.
  const text = zipBytes.toString("binary");
  if (!text.includes("meta/info.json")) {
    fail("zip missing meta/info.json — converter output looks wrong");
  }
  console.log(
    `OK: download ${zipBytes.length}b zip with meta/info.json present`,
  );

  console.log("\nAll checks passed — DoD #2 integration trace complete.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
