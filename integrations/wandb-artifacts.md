# Integration: log converted dataset as W&B Artifact on `job.completed`

**Cycle**: AB · Wave 3 sequential
**Date**: 2026-05-14
**Status**: DRAFT integration guide. Reference implementation; not a feature agibridge runs server-side. Customer wires this in their own webhook receiver.
**Audience**: VLA / robotics teams that track training experiments in Weights & Biases and want every converted dataset to land as an Artifact for run-lineage purposes — π0-derivative shops, LBM contributors, GR00T fine-tuners.

---

## 1. What this integrates

When agibridge fires `job.completed` per `integrations/webhook-spec.md` §2.2, the receiver:

1. Verifies the signature with the SDK helper.
2. Downloads the converted dataset from the presigned R2 URL.
3. Constructs a `wandb.Artifact` of `type="dataset"` and logs it to a W&B run.

The Artifact records lineage: every downstream training run that calls `run.use_artifact("<name>:latest")` declares this conversion job as an input. The W&B UI then shows the converted-dataset → trained-model graph, which is the value-prop for W&B shops that complain about "which dataset version produced this model".

---

## 2. Reference implementation (Python, FastAPI receiver)

~70 lines. Uses the agibridge Python SDK for signature verification and the `wandb` library for Artifact construction.

```python
# wandb_receiver.py
import os
import tempfile
import httpx
import wandb
from fastapi import FastAPI, Request, HTTPException
from agibridge import Webhook, AgibridgeSignatureError

app = FastAPI()
AGIBRIDGE_WEBHOOK_SECRET = os.environ["AGIBRIDGE_WEBHOOK_SECRET"]
WANDB_ENTITY = os.environ["WANDB_ENTITY"]    # e.g. "your-org" — your W&B team
WANDB_PROJECT = os.environ["WANDB_PROJECT"]  # e.g. "vla-training"
# WANDB_API_KEY in env → wandb.init() picks it up automatically

@app.post("/agibridge-webhook")
async def receive(request: Request):
    raw = await request.body()
    sig = request.headers.get("agibridge-signature")
    try:
        event = Webhook.construct_event(
            payload=raw, sig_header=sig,
            secret=AGIBRIDGE_WEBHOOK_SECRET, tolerance=300,
        )
    except AgibridgeSignatureError:
        raise HTTPException(status_code=400, detail="bad signature")

    if event.type != "job.completed":
        return {"ok": True, "ignored": event.type}

    job = event.data.object

    # 1. Download converted dataset from agibridge presigned URL.
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with httpx.stream("GET", job.output_url) as r:
            for chunk in r.iter_bytes(chunk_size=8 << 20):
                tmp.write(chunk)
        tmp_path = tmp.name

    # 2. Construct + log the Artifact.
    run = wandb.init(
        entity=WANDB_ENTITY,
        project=WANDB_PROJECT,
        job_type="preprocess",
        name=f"agibridge-{job.id}",
    )
    artifact = wandb.Artifact(
        name=f"lerobot-v3-{job.input_filename.replace('.tar.gz', '')}",
        type="dataset",
        description=f"Converted by agibridge job {job.id} on {job.completed_at}",
        metadata={
            "agibridge_job_id": job.id,
            "agibridge_org_id": job.org_id,
            "input_format": job.input_format,
            "output_format": job.output_format,
            "episode_count": job.episode_count,
            "output_size_bytes": job.output_size_bytes,
            "duration_seconds": job.duration_seconds,
        },
    )
    artifact.add_file(tmp_path, name=f"{job.id}.tar.gz")
    run.log_artifact(artifact)
    run.finish()

    os.unlink(tmp_path)
    return {"ok": True, "artifact_name": artifact.name}
```

API surfaces called above, verified against W&B docs (`docs.wandb.ai/guides/artifacts/construct-an-artifact/` accessed 2026-05-14):

- `wandb.init(entity, project, job_type, name)` — starts a W&B run that owns the Artifact lineage. `job_type="preprocess"` is the conventional value for non-training runs per the wandb docs quote: "The type should be simple, descriptive, and correspond to a single step of your machine learning pipeline."
- `wandb.Artifact(name, type, description, metadata)` — constructor with parameter names exactly per `docs.wandb.ai/guides/artifacts/construct-an-artifact/` accessed 2026-05-14: "name should be unique, descriptive, and easy to remember... type should be simple, descriptive". Metadata is a free-form dict the W&B UI renders.
- `artifact.add_file(local_path, name)` — adds a file with optional rename in the Artifact's logical filesystem. Verbatim from `docs.wandb.ai/guides/artifacts/construct-an-artifact/`: "adds individual files, with optional renaming."
- `run.log_artifact(artifact)` — logs to W&B with versioning. Per the same doc: "Each invocation with matching name and type creates a new version automatically" → second-time invocation for the same input filename creates `:v1`, `:v2`, etc.

### 2.1 Directory variant (multi-file LeRobot v3 dataset)

A LeRobot v3 dataset is structured as a directory (`meta/info.json`, `data/chunk-000/episode_000.parquet`, `videos/...`) rather than a single tarball. If the receiver extracts the agibridge output before logging, use `artifact.add_dir`:

```python
import tarfile

extract_dir = f"./scratch/{job.id}/"
with tarfile.open(tmp_path) as tf:
    tf.extractall(path=extract_dir)

artifact.add_dir(extract_dir)
```

`add_dir(local_path, name=None)` adds an entire directory tree per `docs.wandb.ai/guides/artifacts/construct-an-artifact/` accessed 2026-05-14.

### 2.2 Reference variant (point to R2 URL without re-uploading)

If the customer wants W&B to track the lineage but doesn't want a second copy of the dataset stored in W&B (W&B Artifacts have storage costs — see `docs.wandb.ai/models/artifacts` pricing model accessed 2026-05-14), use `artifact.add_reference` to point at the agibridge R2 URL instead of uploading bytes:

```python
artifact.add_reference(
    uri=job.output_url,
    name=f"{job.id}.tar.gz",
)
```

Per W&B docs: "references external URIs (HTTP, S3, GCS)" — works with the agibridge presigned R2 URL.

**Caveat**: the presigned URL has an expiry (typically 24 hours per agibridge presign defaults). The W&B Artifact reference becomes a dangling pointer after that. Two mitigations: (a) raise the presign expiry via `client.jobs.presign_download(job_id, expires_in=...)` per `sdk/python-spec.md` §3.3 (caps at 7 days by R2 spec); (b) re-presign on demand when the W&B reference is consumed. For long-lived lineage tracking, option (a) is simpler at MVP.

---

## 3. When W&B Artifact makes sense vs agibridge's own job history

This is the actual product-strategy question — "do I need both?"

| Question | agibridge job history | W&B Artifact |
|---|---|---|
| What was the input? | `Job.input_filename`, immutable | `metadata.agibridge_job_id` (lookup back) |
| What was the conversion config? | implicit in agibridge backend version | needs metadata copy |
| What downstream models used this dataset? | unknown to agibridge | first-class W&B lineage graph |
| Did training run X use dataset version Y? | unanswerable | `run.use_artifact("name:v3")` resolves |
| Is the dataset still retrievable? | yes, until retention window (DR-018 #3: Solo 30d / Team 30d / Enterprise 90d / Free 24h) | yes, indefinitely until W&B deletion |

**Rule of thumb**: agibridge is the source-of-truth for the **conversion step**. W&B Artifact is the source-of-truth for **training-run consumption**. Customers with both run-tracking and dataset-conversion concerns should run both — they answer different questions.

**When to skip W&B**: customer's training stack is not W&B (uses MLflow → see `integrations/mlflow-tracking.md`; uses raw filesystem → just use agibridge job history). Logging Artifacts you never consume via `use_artifact` is W&B-billing waste with no lineage payoff.

**When to skip agibridge job history (rely on W&B alone)**: you can't. agibridge's job row is the source of the conversion (which `embodied-data` lib version produced this output, what the input was, etc.). W&B Artifact metadata is a copy by reference, not an authoritative record. If you're going to run conversions through agibridge, the job-history audit trail is implicit; don't try to migrate it into W&B.

---

## 4. Authentication model

Customer's W&B API key (`WANDB_API_KEY`) is read by `wandb.init()` from env per the wandb library's conventional auth flow. Same boundary considerations as `integrations/hf-datasets-push.md` §3:

- **Receiver-on-customer-infra**: customer's env has the key. agibridge never touches it.
- **Receiver-on-agibridge-secret-store**: customer stores the key in `/settings/secrets/WANDB_API_KEY` (encrypted at rest), receiver fetches it at startup via the SDK.

W&B explicitly recommends server-side keys not be embedded in browser bundles — same posture as our SDK `agibridge_sk_*` per `sdk/python-spec.md:38-44`.

---

## 5. Receiver-side idempotency

Same posture as `integrations/hf-datasets-push.md` §4: dedup on `event.id` before processing. Wrinkle for W&B specifically — because `log_artifact` auto-versions on every call (`:v0`, `:v1`, `:v2`), a duplicate webhook delivery creates a noise version with the same content. Receivers care about this for the lineage UI (cluttered with duplicates) and W&B storage billing.

Dedup pattern (same as HF receiver):

```python
if await db.processed_events.exists(event.id):
    return {"ok": True, "duplicate": event.id}
# ... do the W&B log
await db.processed_events.insert(event.id)
```

---

## 6. Sources

**Vendor docs (accessed 2026-05-14)**:

- https://docs.wandb.ai/guides/artifacts — landing reference (redirects to `https://docs.wandb.ai/models/artifacts`)
- https://docs.wandb.ai/guides/artifacts/construct-an-artifact/ — `wandb.Artifact(name, type, description, metadata)` constructor, `add_file(local_path, name)`, `add_dir(local_path)`, `add_reference(uri, name)`, `run.log_artifact(artifact)`, auto-versioning behavior
- https://docs.wandb.ai/ref/python/sdk/classes/artifact/ — ref-level constructor parameters

**Local sources**:

- `integrations/webhook-spec.md` §2.2 — `job.completed` payload shape this receiver parses
- `integrations/webhook-spec.md` §3.1 — at-least-once delivery semantics, dedup posture
- `sdk/python-spec.md` §3.3 — `presign_download(job_id, expires_in)` parameter for §2.2 reference-variant
- `sdk/python-spec.md` §8 — `Webhook.construct_event` SDK helper
- `DECISIONS.md` DR-018 default #3 — retention windows (Free 24h / Solo 30d / Team 30d / Enterprise 90d) that bound how long agibridge job-history lives

---

**Word count** (excluding meta-header, sources, code samples): ~960 words — within 800–1,200 target.
