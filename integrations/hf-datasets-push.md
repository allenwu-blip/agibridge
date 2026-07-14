# Integration: push converted dataset to Hugging Face Hub on `job.completed`

**Cycle**: AB · Wave 3 sequential
**Date**: 2026-05-14
**Status**: DRAFT integration guide. Reference implementation; not a feature agibridge runs server-side. Customer wires this in their own webhook receiver.
**Audience**: PhD students + robotics teams already pushing LeRobot v3 datasets to HF Hub; want to skip the `pip install datasets && lerobot-convert && huggingface-cli upload` loop and have agibridge fire-and-forget the push.

---

## 1. What this integrates

`agibridge` converts an AgiBot World tarball to a LeRobot v3 dataset (or the reverse) on its backend. When the job hits terminal `done` state, agibridge fires a `job.completed` webhook per `integrations/webhook-spec.md` §2.2. Receiver:

1. Verifies the signature with `Webhook.construct_event` per `sdk/python-spec.md` §8.
2. Pulls the converted artifact via the presigned R2 URL (`output_url` field).
3. Pushes the artifact to the customer's HuggingFace Hub repo.

The integration is **customer-side**, not server-side. agibridge never holds the customer's HF token; the customer's webhook receiver does. This is the right default — see §5 auth model.

---

## 2. Reference implementation (Python, FastAPI receiver)

Minimum-viable receiver. ~60 lines. Uses the agibridge Python SDK for signature verification and the `huggingface_hub` library for upload.

```python
# webhook_receiver.py
import os
import tempfile
import httpx
from fastapi import FastAPI, Request, HTTPException
from agibridge import Webhook, AgibridgeSignatureError
from huggingface_hub import HfApi

app = FastAPI()
hf_api = HfApi(token=os.environ["HF_TOKEN"])
AGIBRIDGE_WEBHOOK_SECRET = os.environ["AGIBRIDGE_WEBHOOK_SECRET"]
HF_REPO_OWNER = os.environ["HF_REPO_OWNER"]  # e.g. "your-org" or your username

@app.post("/agibridge-webhook")
async def receive(request: Request):
    raw = await request.body()
    sig = request.headers.get("agibridge-signature")
    try:
        event = Webhook.construct_event(
            payload=raw,
            sig_header=sig,
            secret=AGIBRIDGE_WEBHOOK_SECRET,
            tolerance=300,
        )
    except AgibridgeSignatureError:
        raise HTTPException(status_code=400, detail="bad signature")

    if event.type != "job.completed":
        return {"ok": True, "ignored": event.type}

    job = event.data.object
    repo_id = f"{HF_REPO_OWNER}/{job.input_filename.replace('.tar.gz', '')}"

    # 1. Ensure dataset repo exists (idempotent).
    hf_api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)

    # 2. Download converted dataset from agibridge presigned URL to a temp file.
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with httpx.stream("GET", job.output_url) as r:
            for chunk in r.iter_bytes(chunk_size=8 << 20):  # 8 MiB
                tmp.write(chunk)
        tmp_path = tmp.name

    # 3. Push to HF Hub.
    hf_api.upload_file(
        path_or_fileobj=tmp_path,
        path_in_repo=f"lerobot_v3/{job.id}.tar.gz",
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"agibridge job {job.id}: converted {job.episode_count} episodes",
    )

    os.unlink(tmp_path)
    return {"ok": True, "repo_id": repo_id}
```

API surfaces called above, verified against `huggingface_hub` docs (`huggingface.co/docs/huggingface_hub/guides/upload` accessed 2026-05-14):

- `HfApi(token=...)` — constructor takes auth token; alternatively reads from `huggingface-cli login` cached creds.
- `hf_api.create_repo(repo_id, repo_type="dataset", exist_ok=True)` — repo creation is idempotent via `exist_ok` (verified against `huggingface.co/docs/huggingface_hub/guides/upload` accessed 2026-05-14, which documents `api.run_as_future(api.create_repo, "username/my-model", exists_ok=True)`).
- `hf_api.upload_file(path_or_fileobj, path_in_repo, repo_id, repo_type, commit_message)` — parameter names quoted verbatim from `huggingface.co/docs/huggingface_hub/guides/upload` accessed 2026-05-14: "Specify the path of the file to upload, where you want to upload the file to in the repository, and the name of the repository."

### 2.1 Large-dataset variant (>5 GB after conversion)

LeRobot v3 datasets for tasks like AgiBot World can run >10 GB after conversion. `upload_file` works but creates a single LFS object per call; for resumable + multi-threaded upload of a folder, swap step 3 for `upload_large_folder` (extract the tarball first):

```python
import tarfile

with tarfile.open(tmp_path) as tf:
    tf.extractall(path=f"./scratch/{job.id}/")

hf_api.upload_large_folder(
    repo_id=repo_id,
    repo_type="dataset",
    folder_path=f"./scratch/{job.id}/",
)
```

Per `huggingface.co/docs/huggingface_hub/guides/upload` (accessed 2026-05-14): "upload_large_folder is resumable... multi-threaded... resilient to errors." Important caveat from same doc: "you cannot set a custom `commit_message` and `commit_description` since multiple commits are created" — the job metadata (job id, episode count) should go in the dataset card README instead of the commit message.

---

## 3. Authentication model

### 3.1 Customer provides HF token via agibridge dashboard secret store (recommended)

Customer adds their HF token under `/settings/secrets/HF_TOKEN` in the agibridge dashboard. agibridge stores the token encrypted (AES-256-GCM, key in env var, not in DB). When dispatching the `job.completed` webhook, agibridge does NOT include the token in the payload — the receiver code reads it from its own env or fetches it from the agibridge `GET /api/v1/secrets/HF_TOKEN` endpoint authenticated by the SDK API key.

**Why dashboard-stored not payload-injected**: payload is transmitted across the receiver's TLS endpoint (which the customer controls), but webhook delivery infrastructure logs payloads for delivery debugging (§7 of webhook-spec.md). Putting the HF token in the payload means it shows up in our delivery dashboard, which is the wrong trust boundary. Keep secrets in the secret store, addressable by name.

### 3.2 Customer provides HF token via their own env (simpler, recommended for self-hosted receivers)

The reference receiver above uses `os.environ["HF_TOKEN"]`. If the receiver runs on the customer's infra (their Modal deployment, their Fly app, their k8s cluster), this is the right path — agibridge never touches the HF token at all. The dashboard secret store from §3.1 is for serverless-receiver customers who don't want their own env-management layer.

### 3.3 What scope of HF token

Minimum required: `write` access to the target dataset repo. Per HF docs (`huggingface.co/docs/huggingface_hub/guides/upload` accessed 2026-05-14), the `upload_file` and `upload_large_folder` operations require write scope. Recommend customers provision a dedicated token scoped to the dataset repo, NOT their account-wide `write` token — least-privilege failure surface.

---

## 4. Receiver-side idempotency

`integrations/webhook-spec.md` §3.1 guarantees at-least-once delivery. The receiver above is NOT idempotent — calling `upload_file` twice for the same job appends two commits with the same `path_in_repo`, which overwrites silently (per HF docs: "If the file already exists, the file contents are overwritten" — `huggingface.co/docs/huggingface_hub/guides/upload` accessed 2026-05-14 on `CommitOperationAdd`). For most cases this is fine (idempotent at-rest state) but creates noisy git history on the HF repo.

For cleaner history, dedup on `event.id` before processing:

```python
# Persist seen event ids; SELECT-then-skip OR INSERT-IGNORE.
@app.post("/agibridge-webhook")
async def receive(request: Request):
    ...
    event = Webhook.construct_event(...)
    if await db.processed_events.exists(event.id):
        return {"ok": True, "duplicate": event.id}
    try:
        # ... do the HF push
        await db.processed_events.insert(event.id)
    except Exception:
        raise  # let agibridge retry
```

Same pattern as our own inbound Stripe handler at `stripe_webhook_spec.md` §1 (idempotency via PK on `stripe_events.id`).

---

## 5. When this integration makes sense

**Yes**: customer's primary dataset distribution is HF Hub. The "convert and publish" cycle is the bottleneck and they want it removed entirely. Common pattern for VLA labs that publish π0-derivative training data, PhD students sharing converted datasets with collaborators, OSS robotics datasets posted under permissive licenses.

**No**: customer's converted dataset is internal-only (proprietary collected data) and HF Hub is not part of their pipeline. Use the W&B Artifact (`integrations/wandb-artifacts.md`) or MLflow (`integrations/mlflow-tracking.md`) integration patterns instead — both keep the dataset in the customer's training-platform of record without exposing it on a public hub.

**Maybe**: customer has both public + private datasets. Per HF docs (`huggingface.co/docs/datasets/upload_dataset` accessed 2026-05-14): "To set your dataset as private, set the `private` parameter to `True`. This parameter will only work if you are creating a repository for the first time." Receiver pre-creates the repo with `private=True` once; subsequent `upload_file` calls inherit the privacy setting.

---

## 6. Sources

**Vendor docs (accessed 2026-05-14)**:

- https://huggingface.co/docs/datasets/upload_dataset — `push_to_hub` shape, `private=True` parameter, `huggingface-cli login` flow
- https://huggingface.co/docs/huggingface_hub/guides/upload — `HfApi.upload_file` parameter names (`path_or_fileobj`, `path_in_repo`, `repo_id`, `repo_type`, `commit_message`), `upload_large_folder` resumability + multi-thread behavior, `CommitOperationAdd` overwrite semantics, `create_repo(exist_ok=True)` idempotency
- https://huggingface.co/docs/huggingface_hub/index — library landing reference

**Local sources**:

- `integrations/webhook-spec.md` §2.2 — `job.completed` payload shape this receiver parses
- `integrations/webhook-spec.md` §3.1 — at-least-once delivery semantics
- `integrations/webhook-spec.md` §7 — delivery dashboard surface
- `sdk/python-spec.md` §8 — `Webhook.construct_event` helper
- `app/api/stripe_webhook_spec.md` §1 — idempotency pattern via PK on event_id, mirrored in §4 above

---

**Word count** (excluding meta-header, sources, code samples): ~870 words — within 800–1,200 target.
