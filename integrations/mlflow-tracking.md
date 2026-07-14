# Integration: log dataset metadata to MLflow tracking on `job.completed`

**Cycle**: AB · Wave 3 sequential
**Date**: 2026-05-14
**Status**: DRAFT integration guide. Reference implementation; not a feature agibridge runs server-side. Customer wires this in their own webhook receiver.
**Audience**: classical-robotics + ROS-aligned teams that track training experiments in MLflow rather than W&B. AV perception teams (Argo-style), manipulation labs with on-prem MLflow servers, university research groups consuming the Databricks MLflow tier.

---

## 1. What this integrates

When agibridge fires `job.completed` per `integrations/webhook-spec.md` §2.2, the receiver:

1. Verifies the signature via the SDK helper.
2. Downloads the converted dataset from the presigned R2 URL.
3. Logs the dataset as an MLflow artifact under an MLflow run and tags the run with agibridge job metadata.

MLflow's dataset-tracking story is split across two APIs — `mlflow.log_artifact` (the bytes) and `mlflow.log_input` (the dataset metadata as a first-class entity). Most embodied-AI workflows want both: the tarball as a downloadable artifact, the metadata as a queryable dataset record.

---

## 2. Reference implementation (Python, FastAPI receiver)

~80 lines. Uses the agibridge Python SDK for signature verification and the `mlflow` library for tracking.

```python
# mlflow_receiver.py
import os
import tempfile
import httpx
import mlflow
from fastapi import FastAPI, Request, HTTPException
from agibridge import Webhook, AgibridgeSignatureError

app = FastAPI()
AGIBRIDGE_WEBHOOK_SECRET = os.environ["AGIBRIDGE_WEBHOOK_SECRET"]
MLFLOW_TRACKING_URI = os.environ["MLFLOW_TRACKING_URI"]  # e.g. http://mlflow.lab.internal:5000
MLFLOW_EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT", "agibridge-conversions")

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
mlflow.set_experiment(MLFLOW_EXPERIMENT)

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

    # 1. Download converted dataset.
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with httpx.stream("GET", job.output_url) as r:
            for chunk in r.iter_bytes(chunk_size=8 << 20):
                tmp.write(chunk)
        tmp_path = tmp.name

    # 2. Open an MLflow run, log the artifact + tags.
    with mlflow.start_run(run_name=f"agibridge-{job.id}") as run:
        # 2a. Run-level tags (queryable via mlflow.search_runs)
        mlflow.set_tag("agibridge.job_id", job.id)
        mlflow.set_tag("agibridge.org_id", job.org_id)
        mlflow.set_tag("agibridge.input_format", job.input_format)
        mlflow.set_tag("agibridge.output_format", job.output_format)
        mlflow.set_tag("agibridge.input_filename", job.input_filename)

        # 2b. Run-level metrics (auditable conversion stats)
        mlflow.log_metric("episode_count", job.episode_count)
        mlflow.log_metric("output_size_bytes", job.output_size_bytes)
        mlflow.log_metric("duration_seconds", job.duration_seconds)

        # 2c. The actual dataset bytes as an artifact.
        mlflow.log_artifact(
            local_path=tmp_path,
            artifact_path="lerobot_v3",
        )

        # 2d. Dataset entity for first-class queryability.
        dataset = mlflow.data.meta_dataset.MetaDataset(
            source=mlflow.data.http_dataset_source.HTTPDatasetSource(url=job.output_url),
            name=job.input_filename.replace(".tar.gz", ""),
            digest=job.id,  # job.id acts as content digest for our purposes
        )
        mlflow.log_input(dataset, context="conversion")

    os.unlink(tmp_path)
    return {"ok": True, "mlflow_run_id": run.info.run_id}
```

API surfaces called above, verified against MLflow docs:

- `mlflow.set_tracking_uri(uri)` — points the client at a tracking server. Standard MLflow setup (`mlflow.org/docs/latest/tracking/` accessed 2026-05-14).
- `mlflow.set_experiment(name)` — sets the active experiment; creates it if missing. Standard MLflow setup.
- `mlflow.start_run(run_name=..., experiment_id=..., nested=..., tags=..., description=..., parent_run_id=...)` — parameter names verbatim per Context7 MLflow API reference (`github.com/mlflow/mlflow/blob/master/docs/api_reference/source/python_api/mlflow.md` accessed 2026-05-14): "you can specify parameters such as `run_id` to resume an existing run, `experiment_id` to associate the run with a specific experiment, `run_name` for a custom name... `nested` to create a nested run."
- `mlflow.set_tag(key, value, synchronous=None)` — signature verbatim per `github.com/mlflow/mlflow/blob/master/docs/api_reference/source/python_api/mlflow.md` accessed 2026-05-14: "Set a tag under the current run. If no run is active, this method will create a new active run."
- `mlflow.log_metric(key, value, step=None, synchronous=None, timestamp=None, run_id=None, model_id=None, dataset=None)` — full signature per the same source.
- `mlflow.log_artifact(local_path, artifact_path=None, run_id=None)` — signature per the same source: "Log a local file as an artifact of the currently active run."
- `mlflow.log_input(dataset, context=None, tags=None, model=None)` — signature per the same source. Logs an `mlflow.data.dataset.Dataset` instance with a `context` string (e.g. `"training"`, `"validation"`, `"conversion"`).

### 2.1 Tags-only variant (skip the artifact upload)

If MLflow is the customer's training-tracking backbone but the converted dataset already lives on their object store via agibridge's R2 (or downstream via the HF integration in `integrations/hf-datasets-push.md`), tags-and-input alone are enough. Skip §2c (`log_artifact` of the tarball) and just record metadata:

```python
with mlflow.start_run(run_name=f"agibridge-{job.id}"):
    mlflow.set_tag("agibridge.job_id", job.id)
    mlflow.set_tag("agibridge.org_id", job.org_id)
    mlflow.set_tag("agibridge.output_url", job.output_url)
    mlflow.log_metric("episode_count", job.episode_count)
    mlflow.log_metric("duration_seconds", job.duration_seconds)
```

This is the leanest possible integration — ~10 lines of receiver work, no dataset bytes transit through the MLflow tracking server. Use when the MLflow tracking server is bandwidth-constrained (on-prem deployments often are) or when the dataset is too large for the MLflow artifact store to make sense.

### 2.2 Using `mlflow.data.from_pandas` if the dataset has a pandas-loadable index

MLflow's first-class dataset entity is `mlflow.data.from_pandas`. If the customer's pipeline can produce a small index DataFrame (one row per episode with `episode_id`, `path_in_tarball`, `task_label`) from the LeRobot v3 dataset, they can log it as the canonical MLflow dataset:

```python
import pandas as pd
index_df = build_index_from_lerobot_dataset(extracted_path)  # customer code
dataset = mlflow.data.from_pandas(
    index_df,
    source=job.output_url,
    name=f"lerobot-v3-{job.id}",
    targets=None,  # no single target column for VLA index
)
with mlflow.start_run():
    mlflow.log_input(dataset, context="conversion")
```

Signature verbatim per Context7 MLflow docs: `mlflow.data.from_pandas(df, source, name, targets)` (`github.com/mlflow/mlflow/blob/master/docs/docs/classic-ml/dataset/index.mdx` accessed 2026-05-14). The example in those docs uses the wine-quality CSV pattern; the receiver above swaps that for a LeRobot v3 episode index.

---

## 3. When MLflow integration makes sense vs W&B

| Surface | MLflow | W&B |
|---|---|---|
| Self-hostable open-source server | yes (default) | self-hosted is enterprise tier only |
| ROS / classical-robotics ecosystem fit | strong (used by lots of ROS-aligned labs, NVIDIA Isaac docs) | weaker (W&B is more VLA-shop-aligned) |
| First-class dataset entity (`log_input`) | yes | Artifact mechanism (different model — see `integrations/wandb-artifacts.md` §3) |
| Hosted SaaS | Databricks (paid) | W&B SaaS (paid) |
| Lineage graph UI | basic (tags-based, no first-class graph) | first-class (`run.use_artifact` graph) |
| Cost at scale | self-host = $0 server cost | per-seat + per-storage |

**Pick MLflow when**: your training-pipeline is already MLflow-instrumented (commonly inherited from classical-ML / ROS perception teams), or you want self-hosted with no third-party dependency, or you're on Databricks.

**Pick W&B when**: your training-pipeline is VLA-shop-style (π0 / GR00T / LBM derivatives) and you specifically need the lineage graph UI. See `integrations/wandb-artifacts.md` §3 for that decision matrix.

**Pick both**: rare but valid — `log_artifact` + `log_input` in MLflow for the audit trail, `wandb.Artifact` for the training-run lineage. The receiver above can be extended to fire both in the same handler. Cost is one extra `wandb.init` block; semantic redundancy is acceptable because MLflow and W&B answer different questions (run-history queryability vs lineage-graph visualization).

---

## 4. Authentication model

MLflow tracking servers fall into three auth regimes; the receiver above covers all three by reading config from env:

- **No auth** (default OSS MLflow server) — `MLFLOW_TRACKING_URI` alone is enough. Common on internal-only LANs.
- **Basic auth** — `MLFLOW_TRACKING_USERNAME` + `MLFLOW_TRACKING_PASSWORD` env vars. MLflow client picks these up automatically per the MLflow auth docs.
- **Databricks workspace** — `DATABRICKS_HOST` + `DATABRICKS_TOKEN` env vars. Setting `MLFLOW_TRACKING_URI=databricks` activates this path.

Same boundary considerations as `integrations/hf-datasets-push.md` §3 and `integrations/wandb-artifacts.md` §4: prefer customer-side env over agibridge-stored secret if the receiver runs on customer infra.

---

## 5. Receiver-side idempotency

`mlflow.start_run` without a `run_id` creates a new run every call. A duplicate webhook delivery (per `integrations/webhook-spec.md` §3.1 at-least-once guarantee) creates two runs for the same agibridge job. Cleaner pattern: dedup on `event.id` before starting the run, same as the HF receiver (`integrations/hf-datasets-push.md` §4) and W&B receiver (`integrations/wandb-artifacts.md` §5):

```python
if await db.processed_events.exists(event.id):
    return {"ok": True, "duplicate": event.id}
with mlflow.start_run(run_name=f"agibridge-{job.id}"):
    # ... log
await db.processed_events.insert(event.id)
```

Alternative: encode `agibridge.job_id` as a search-key tag and check via `mlflow.search_runs(filter_string=f"tags.agibridge.job_id = '{job.id}'")` before logging. Two-call latency (search + log) is acceptable for once-per-conversion frequency.

---

## 6. Sources

**Vendor docs (accessed 2026-05-14)**:

- https://mlflow.org/docs/latest/tracking — landing reference (redirects to `https://mlflow.org/docs/latest/tracking/`)
- https://mlflow.org/docs/latest/tracking/ — `mlflow.set_tracking_uri`, `mlflow.set_experiment`, run-lifecycle patterns
- https://mlflow.org/docs/latest/python_api/mlflow.html — `mlflow.log_artifact(local_path, artifact_path, run_id)` signature, `mlflow.log_metric(key, value, step, synchronous, timestamp, run_id, model_id, dataset)` signature
- https://github.com/mlflow/mlflow/blob/master/docs/api_reference/source/python_api/mlflow.md — `mlflow.set_tag(key, value, synchronous)` full signature with parameter constraints, `mlflow.start_run(run_id, experiment_id, run_name, nested, tags, description, parent_run_id)` parameter set
- https://github.com/mlflow/mlflow/blob/master/docs/docs/classic-ml/dataset/index.mdx — `mlflow.data.from_pandas(df, source, name, targets)` signature, `mlflow.log_input(dataset, context, tags, model)` usage pattern

**Local sources**:

- `integrations/webhook-spec.md` §2.2 — `job.completed` payload this receiver parses
- `integrations/webhook-spec.md` §3.1 — at-least-once delivery
- `integrations/hf-datasets-push.md` §4, `integrations/wandb-artifacts.md` §5 — shared idempotency pattern
- `integrations/wandb-artifacts.md` §3 — W&B-vs-MLflow decision matrix (this guide cross-references it)
- `sdk/python-spec.md` §8 — `Webhook.construct_event` SDK helper

---

**Word count** (excluding meta-header, sources, code samples): ~1,030 words — within 800–1,200 target.
