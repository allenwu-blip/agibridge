# Voice guide for agibridge surfaces

This is the canonical voice register for **README**, **landing copy**, **in-app error toasts**, **status banners**, and any researcher-facing string the frontend ships. The 30-lab outbound DM angles in `_workspace/outbound-30-labs.md` are the source-of-truth peer-collaboration register; everything user-facing in agibridge matches that register.

The Phase D resolution A4.2 named voice consistency across three surfaces as a DoD check (item 6). This file is the contract.

## Register: peer-collaboration, not customer-support

agibridge is a hobby project from one researcher to other researchers. The reader is a peer who knows what HDF5, fps, and frame-video alignment mean. We talk to them as such.

### Banned phrases (CI greppable)

| Phrase | Why banned |
|---|---|
| `we'll get back to you` | Implies a support queue with SLA. There is none. |
| `thank you for your patience` | Customer-service register; condescending to researchers. |
| `our team` | There is no team. One maintainer. Hobby framing. |
| `support ticket` | This is a GitHub issues surface, not a ticket queue. |
| `please contact us` | Same — wrong register, wrong infrastructure. |
| `we apologize for the inconvenience` | Customer-service template. Don't. |

(See `_workspace/f1-banned-words.regex` for the F-1 hobby-framing list — that's separate, broader, and CI-enforced.)

### Required pattern phrases

These appear naturally in the surfaces below; ship copy that uses them.

| Phrase | Where it shows up |
|---|---|
| `would value feedback` | Outbound DMs, error toasts when a corner case is suspected |
| `open issues on the embodied-data GitHub` | Error toasts that need a bug report, README "How to file bugs" |
| `contributors welcome` | README footer, Discord welcome, landing follow section |
| `researchers helping researchers` | Discord code of conduct, README "How to file bugs" |
| `paste your session_id` | Hosted-demo error toasts — the session_id is the diagnostic primitive |

## Three surfaces, three samples each (DoD #6 test)

### Surface 1 — README "How to file bugs" (this repo, README.md)

> Open an issue on the embodied-data GitHub — that's where the actual conversion code lives, and that's where contributors will see it.

> If a hosted-demo session failed and you need someone to look at logs, paste the session_id; logs are kept ~7 days on HuggingFace Space's log viewer.

> For schema-shape edge cases, the embodied-data issue tracker is the right venue — researchers helping researchers, not a support queue.

### Surface 2 — In-app error toasts (frontend brief input)

The frontend agent ships error toast strings. They MUST match the register below. Required strings for the toast types named in `backend-architecture-W1.md` §5:

- `converter_rejected_input` (422):
  > **The converter rejected this archive.** Lib message: `<error.message>`. Suggestion: `<error.suggestion>`. If this looks like a bug, open an issue on the [embodied-data GitHub](https://github.com/allenwu-blip/embodied-data/issues) with the session_id and the lib message — contributors welcome.

- `oom_suspected` (503):
  > **The run was stopped, most likely because it exceeded the memory limit on this hosted environment.** Run the CLI locally for full datasets: `pip install embodied-data`. If this happens on a small archive, would value feedback — paste the session_id on the [embodied-data GitHub issues](https://github.com/allenwu-blip/embodied-data/issues).

- `conversion_timeout` (504):
  > **The run took longer than 25 minutes and was stopped.** Smaller slices via `--max-episodes` work; full Beta tasks are best run locally with `pip install embodied-data`. If a smaller slice still times out, open an issue with the session_id.

(Frontend agent: these are the canonical strings. Adjust placement and component shape, not the register or content.)

### Surface 3 — 30-lab outbound DM angles (`_workspace/outbound-30-labs.md`)

Excerpt — row 9 (Yuke Zhu, NVIDIA GEAR / UT Austin):
> I noticed lerobot#2158 and AgiBot-World#124 keep surfacing the same v2.1 → v3 path on AgiBot data — wrote `embodied-data` (MIT) as a one-command bidirectional AgiBot ↔ LeRobot v3 converter with a 5-check validator. Open question: does the GEAR pre-training pipeline already ingest AgiBot natively, or does it route through a custom converter? **Would value any feedback** if a student runs into edge cases the 5 checks miss.

Excerpt — row 11 (Chelsea Finn, Stanford IRIS):
> If anyone using BEHAVIOR also wants to mix AgiBot data, the validator might catch the type of misalignment that breaks training silently.

Excerpt — row 12 (Pulkit Agrawal, MIT Improbable AI):
> If a student in the lab is folding AgiBot into a DART-style mixture, **would value any feedback** on edge cases the validator misses.

## Tonal-consistency check (run before merging copy)

Pull 3 representative strings from each surface above, look at them side by side, and answer: **does the same person sound like they wrote all 9?** If one feels like customer support and another feels like a peer DM, the customer-support one is wrong. Rewrite it.

This is a DoD item (#6 of brief). Code Reviewer enforces.

## Open questions for Allen / frontend-dev

1. The hosted demo's "warming up after 48 h idle" banner copy — frontend brief output should align with this voice guide. If frontend ships something off-register, file a DR.
2. The HF Space `short_description` field is capped at ~120 chars; the version below is in `agibridge/hf-space/README.md`. Confirm that copy passes Allen's smell test before HF push.
