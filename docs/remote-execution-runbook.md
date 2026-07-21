# Remote Execution Runbook

Operator guide for rolling out distributed agent execution across home devices
(Mac mini, Raspberry Pi) with the company laptop as the only machine that can
reach the LLM provider.

Design spec: [superpowers/specs/2026-07-18-distributed-agent-execution-design.md](superpowers/specs/2026-07-18-distributed-agent-execution-design.md)

## 1. Overview

Workers on home devices pull claimed executions from the video-hive server over
the tailnet, run the `pi` CLI locally, and post results back over HTTP. All LLM
traffic flows **worker → tailnet → laptop gateway → 中台**; the 中台 credential
is injected by the gateway on the laptop and never leaves it. Workers hold no
persistent state and store no secrets.

**Scope note.** The remote-execution architecture is generic: the server treats
every worker as a "cloud agent" reachable via the claim protocol, and each agent
may run its own independent LLM endpoint. Only two things in this runbook are
workarounds for our specific constraints, not architectural requirements:

- the **LLM gateway** — needed solely because 中台 is reachable only from the
  company network (disappears entirely with per-worker BYO models);
- the **tailnet** — needed solely because the laptop and home devices are behind
  separate NATs (replaced by plain TLS + worker token if the control plane is
  ever publicly reachable).

Components:

- `server/app/executors/remote_broker.py` — claim/heartbeat/requeue broker. The
  queue is sqlite-backed (`remote_executions` table in the jobs database), so
  queued/claimed executions survive a server restart and stale claims are
  requeued by the sweep after `claim_timeout_seconds`.
- `server/app/routes/remote.py` — authenticated `/api/remote/*` worker endpoints.
- `server/app/executors/sweeper.py` — lease-hygiene sweeper (see §7).
- `scripts/remote/worker.py` — stdlib-only worker agent (one file, copy to device).
- `scripts/remote/llm_gateway.py` — laptop-side credential-injecting proxy.

## 2. Prerequisites checklist

Verify all five spec preconditions **before** any rollout step:

1. **Compliance sign-off** — prompts and model responses physically transit
   personal home devices. Transport is WireGuard-encrypted, but encrypted
   transport is not policy approval. Hard blocker; confirm first.
2. **Tailscale installable on the company laptop** (fallback: domestic VPS
   running frp or Headscale as relay — see §3).
3. **中台 exposes an OpenAI-compatible HTTP API** so the gateway can proxy it
   without protocol translation.
4. **中台 tolerates ~100 concurrent requests from a single token/IP** — confirm
   with the provider; the design does not solve rate limits.
5. **Laptop stays awake during production runs** — `caffeinate -dims`, or AC
   power with display/system sleep disabled and no lid-close sleep.

## 3. Networking (Tailscale)

Install Tailscale on all three devices (laptop, Mac mini, Raspberry Pi) and
bring them up:

```bash
tailscale up
```

Verify connectivity from each worker device to the laptop:

```bash
tailscale ping <laptop-tailnet-ip>
tailscale status
```

Check `tailscale status` output for the laptop peer: it should show `direct`
(P2P NAT traversal working), not `relay ...` (traffic is bouncing off DERP
relays, likely overseas — latency rises from ~10–30 ms to a few hundred ms).
If P2P proves unstable, switch the relay layer to a domestic VPS running
Headscale + DERP or frp tunnels; every other component in this runbook is
transport-agnostic and unchanged.

The laptop's tailnet IPv4 address (`tailscale ip -4`, a `100.x.y.z` address) is
`<laptop-tailnet-ip>` in every command below.

## 4. LLM gateway on the laptop

The gateway binds the tailnet interface only, accepts `POST /v1/*`, and injects
the 中台 `Authorization: Bearer` header. Start it on the laptop from the repo:

```bash
REMOTE_LLM_UPSTREAM="https://<zhongtai-base-url>" REMOTE_LLM_KEY="<zhongtai-key>" \
  uv run python scripts/remote/llm_gateway.py --host <laptop-tailnet-ip> --port 8788
```

Both environment variables are required; the gateway refuses to start without
them. Do not inline real keys into shared terminal history — export them from
a local-only shell or a `.env` you `source` first.

Verify from a worker device:

```bash
curl -X POST http://<laptop-tailnet-ip>:8788/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"ping"}],"stream":false}'
```

Expect a normal OpenAI-compatible chat completion response. A `502` means the
laptop could not reach 中台 (see §10).

## 5. Worker device setup

On each worker device (Mac mini, Raspberry Pi):

1. Install `python3` (3.10+) and the `pi` CLI per the pi CLI's own docs.
2. Copy `scripts/remote/worker.py` to the device — it is a single stdlib-only
   file with no pip dependencies:
   ```bash
   scp scripts/remote/worker.py <device>:~/worker.py
   ```
3. Configure the pi CLI's provider `base_url` to point at the laptop gateway,
   per the pi CLI's own provider config documentation:
   `http://<laptop-tailnet-ip>:8788/v1`. No API key is needed on the device —
   the gateway injects the credential.
4. Smoke-test one manual `pi` run on the device (a one-line prompt through the
   configured provider). This validates §3 + §4 + the pi provider config before
   any server wiring.

## 6. Server configuration

Operator action on the laptop — **do not commit machine-specific values**.

Add a remote executor and the `remote` runtime section to `config/workflow.yaml`:

```yaml
executors:
  pi-remote:
    kind: remote
    global_capacity: 100   # total remote slots across all workers, 200 MB RAM each
    capabilities:
      generate_key_info:
        skill: question_comprehension_info/generate_key_info
        tools: [read, write, bash]
        # optional: only dequeue this capability to workers whose labels match
        requires_labels:
          mem_gb: ">=16"    # numeric comparisons exist only as ">=<int>"
          device: mac-mini  # any other value is a literal equality match
      # ... mirror every capability you intend to run remotely
remote:
  claim_timeout_seconds: 120
  requeue_limit: 3
  min_worker_protocol_version: 1
workflows:
  submit_max_workers: null  # max(4, ceil(largest remote capacity / 2))
```

(`remote.max_archive_bytes` also exists, default 64 MiB; raise only if result
archives legitimately exceed it.)

Set the pre-shared worker token in `.env`:

```bash
# generate once, e.g. openssl rand -hex 32
VIDEO_HIVE_REMOTE_WORKER_TOKEN=<random-token>
```

Startup validation fails fast if any `kind: remote` executor is defined without
the token, so a misconfigured server never starts half-open. Restart the server
after these changes.

### Issuing per-worker tokens

The static token doubles as the **management token**: it is the only
credential accepted by the worker-token issuance and revocation endpoints.
Issue each worker its own revocable token (returned exactly once — only the
sha256 hash of the secret part is stored server-side):

```bash
curl -sS -X POST http://localhost:8000/api/remote/workers/register \
  -H "X-Worker-Token: $VIDEO_HIVE_REMOTE_WORKER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"worker_id": "mac-mini-1", "name": "Mac mini",
       "capabilities": ["generate_key_info", "review_key_info"], "slots": 1}'
# => {"worker_token": "mac-mini-1.<secret>"}
```

Re-issuing for the same `worker_id` rotates the token (the old secret stops
working immediately) and re-onboards a previously revoked worker. You normally
do not run this by hand — workers exchange the management token themselves at
startup (§7).

The static token is management-only. Worker-facing endpoints accept only the
revocable per-worker token issued at startup.

### Submit pool sizing

Remote submissions share `workflows.submit_max_workers`; when unset it derives
as `max(4, ceil(<largest remote executor capacity> / 2))`. Bundle construction
does not renew its lease, so deployments must satisfy:

    ceil(capacity / submit_max_workers) × bundle_build_seconds < lease_ttl_seconds

### Artifact store

Remote runs exchange payloads through a content-addressed artifact store
instead of inline bundle contents:

- **Layout:** blobs live under `data/artifacts/<hash[:2]>/<sha256>`; uploads
  are fsynced inside `data/artifacts/.staging/` and published atomically
  (`os.replace`), so a crash never leaves a half-written blob. The jobs
  database holds two tables: `artifacts` (hash → size) and `artifact_refs`
  (job_id, node_key, name → hash) — the reference list any GC builds on.
- **Endpoints:** `POST /api/artifacts` (raw request body → `{"hash": ...}`;
  413 beyond `remote.max_archive_bytes`) and `GET /api/artifacts/{hash}`
  (404 for malformed or unknown hashes). Both authenticate like the other
  worker-facing endpoints (per-worker token only).
- **Refs-only bundles:** at submit time the server puts declared inputs into
  the store and marks the claim manifest `bundle_mode: "refs"` with
  `input_artifacts` (`name → "sha256:<hash>"`); the bundle then skips those
  payloads and the worker downloads them via `GET /api/artifacts`. For
  results the worker uploads outputs first and reports with an
  `output_artifacts` map; the server rejects unknown refs with 409 (`upload
  output artifact first`). Staging failures are hard submission failures;
  content-carrying bundles and archive-output fallback are removed.
- **GC baseline:** deleting a job removes its `artifact_refs` rows. There is
  no orphan sweep for unreferenced blobs yet — that is a known debt tracked
  in [issues/open/044-P2-artifact-gc-orphans.md](../issues/open/044-P2-artifact-gc-orphans.md).
  Until it lands, `data/artifacts` grows monotonically; watch disk usage on
  long-running servers.

## 7. Launching workers

One worker process per concurrent execution slot. Example: fill a Mac mini
(16 GB → ~65 slots, see capacity rule below):

```bash
export REMOTE_WORKER_REGISTER_TOKEN='<random-token>'   # same value as server .env
for i in $(seq 1 65); do
  nohup python3 worker.py --server http://<laptop-tailnet-ip>:8000 \
    --register-token "$REMOTE_WORKER_REGISTER_TOKEN" --worker-id "mac-mini-$i" \
    --name "Mac mini" --slots 1 \
    --capabilities generate_key_info,review_key_info \
    --work-dir ~/remote-worker >> ~/remote-worker.log 2>&1 &
done
```

With `--register-token` (env `REMOTE_WORKER_REGISTER_TOKEN`) each worker
exchanges the management token for its own per-worker token at startup and
runs the main loop with it — the per-worker token is what the server
authenticates, and it can be revoked per device (§11). `--register-token` is
required; the legacy `--token` self-registration path no longer exists.

`--worker-id` defaults to the hostname; `--capabilities` is a comma-separated
list and must be a subset of the executor's declared capabilities. See
`python3 worker.py --help` for all flags (`--poll-interval`, etc.).

`--label KEY=VALUE` (repeatable) attaches string labels to the worker. Labels
are reported at register time and on every claim, and the broker only
dequeues an execution to a worker whose labels satisfy the capability's
`requires_labels` constraints (§6) — e.g. a capability requiring
`mem_gb: ">=16"` never lands on a worker launched without that label or with
a smaller value. Labels are routing metadata, not credentials.

**Capacity rule:** budget 200 MB RAM per agent process (measured pi RSS: settled
~150 MB, peak ~187 MB — see §9). Mac mini 16 GB → ~65 slots; Raspberry Pi 16 GB
→ ~70 slots; leave the rest as OS headroom. A process manager
(launchd/systemd/tmux) is the operator's choice; the `nohup` loop above is the
minimal viable form.

The sum of slots across all devices must not exceed `global_capacity` on the
`pi-remote` executor — leases enforce it, but oversubscribing wastes polling
cycles.

Every finished node run records its runner (executor id for local runs, the
concrete `worker_id` for remote runs) and shows it in the job detail view;
`GET /api/remote/workers` lists the registered workers with their labels,
last-seen times, and revocation flags (read-only page endpoint, no worker
token; 503 while remote execution is disabled).

### Upgrade order: server first, then workers

Upgrade the server first. It rejects old claims with HTTP 409 through the
worker protocol handshake, so remote processing pauses safely while workers
are upgraded; local execution is unaffected. Then copy the current
`worker.py` to every device and restart it with `--register-token`.
`remote.min_worker_protocol_version` defaults to 1; setting it to 0 disables
the handshake and is only a temporary incident escape hatch because it can
admit workers that do not understand refs-only bundles.

Rows enqueued before the v023 schema migration carry no stored
`command_spec`. A current worker claiming such a row fails immediately with
`server did not provide command_spec; upgrade the server first` and never
reports, so the claim times out, the broker requeues the row
(`remote.claim_timeout_seconds`), and after `remote.requeue_limit` attempts
finishes it as failed — the queue converges on its own, at the cost of a few
failed jobs to rerun. To avoid the churn, drain the remote queue before
upgrading workers across the v023 boundary.

### Sweeper deployment

The sweeper owns all lease hygiene: requeueing expired remote claims,
expiring stale leases, recovering orphaned running jobs, and **renewing the
leases backing live remote executions**. By default one sweeper runs inside
the server process (`sweeper_enabled: true`).

For multi-replica server deployments, disable the in-process sweeper on every
API replica in `config/workflow.yaml` and run the sweeper as a dedicated
process instead:

```yaml
sweeper_enabled: false        # API replicas only
sweeper_interval_seconds: 5   # keep far below lease_ttl_seconds (default 90)
```

Start the dedicated sweeper from the repo root, against the same
configuration and `data/` directory as the API replicas (the database schema
must already exist — start it after an API server has initialized the data
directory at least once):

```bash
UV_CACHE_DIR=.uv-cache uv run python - <<'EOF'
import threading

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.remote_broker import RemoteExecutionBroker
from server.app.executors.sweeper import SweeperThread
from server.app.jobs import JobQueries
from server.app.remote_wiring import register_remote_completion
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import load_settings

settings = load_settings()
job_db = JobQueries(settings.data_dir / "video_hive.sqlite", jobs_dir=settings.jobs_dir)
leases = ExecutorLeaseRepository(job_db.path, job_db=job_db)
broker = RemoteExecutionBroker(
    job_db.path,
    settings.data_dir / "remote_bundles",
    claim_timeout_seconds=settings.executor_runtime.remote.claim_timeout_seconds,
    requeue_limit=settings.executor_runtime.remote.requeue_limit,
)
artifact_store = ArtifactStore(settings.data_dir / "artifacts", job_db.path)
register_remote_completion(broker, leases, settings.jobs_dir, artifact_store)
SweeperThread(
    leases,
    broker,
    interval_seconds=settings.executor_runtime.sweeper_interval_seconds,
    lease_ttl_seconds=settings.executor_runtime.lease_ttl_seconds,
).start()
threading.Event().wait()  # run until killed
EOF
```

Two hard operational requirements:

- **At least one sweeper must be alive at all times.** With none, expired
  claims are never requeued, orphaned jobs never recover, and stale leases
  never expire; and when a sweeper comes back after an outage longer than
  `lease_ttl_seconds`, its first tick expires the aged leases — including ones
  backing remote executions that are still in flight. Running more than one
  sweeper is safe (every step funnels through single-winner sqlite
  transactions), so prefer a supervised process plus a standby over a bare
  `nohup`.
- **`sweeper_interval_seconds` must stay far smaller than `lease_ttl_seconds`.**
  Each tick expires stale leases *before* renewing live remote leases, so with
  `interval ≥ ttl` a healthy remote lease can age past its TTL between renewals
  and be expired by the same tick that was meant to renew it. Keep the interval
  at or below a third of the TTL (defaults: 5 s vs 90 s).

Every API replica registers the completion handler, so a worker report may
land on any replica. A standalone sweeper must register it too, as above,
because lease-expiry cancellation also publishes a completion.

The dedicated sweeper performs all database hygiene but does not publish
realtime job-update events; sweeper-driven transitions surface on the next
client refresh. Single-server deployments should keep the default in-process
sweeper.

## 8. Binding workflows to the remote executor

Use the existing workspace executor configuration to point node capabilities
at `pi-remote` — either in the workspace settings UI or via
`PUT /api/workspaces/{workspace_id}/configuration` with:

- `executor_allocations`: add `{"executor_id": "pi-remote", "concurrency_limit": <slots for this workspace>}`.
- `node_bindings`: for each node to run remotely,
  `{"workflow_key": ..., "node_key": ..., "executor_id": "pi-remote"}`.

The server validates that `pi-remote` declares each bound node's capability
(mismatch is rejected), which is why §6 says to mirror capabilities.

**Rollback:** rebind the same nodes back to executor `pi` (and remove the
`pi-remote` allocation). No other state changes are needed; in-flight remote
executions drain or fail-safe via the requeue semantics in §10.

### Experimental: shard fan-out and reduce fan-in

> **Not production-supported.** Shard/reduce is currently an internal
> experimental capability. Do not add `shard:` or `reduce:` to active
> production workflow revisions. Workflow Studio does not yet preserve these
> declarations when it converts a stored revision back to YAML, and concurrent
> local shards do not yet have isolated output directories. Existing workflows
> continue to use ordinary DAG branching, which already runs independent ready
> nodes concurrently.

A workflow node can fan out across workers by declaring `shard:` in the
workflow definition, and a downstream node can fan the results back in with
`reduce:`:

```yaml
nodes:
  generate:
    capability: generate_key_info
    shard:
      over: inputs.questions  # fan out over a JSON-array input file...
      # count: 100            # ...or over N synthetic {"index": i} inputs
      max_concurrency: 10     # optional scheduling hint per pass
      max_shards: 1000        # optional hard cap, default 1000
  summarize:
    capability: summarize_results
    reduce:
      from: generate
```

Each shard is claimed through the lease system as its own execution — one
lease per shard, so the capacity rules from §6/§7 apply unchanged and a
sharded node bound to `pi-remote` spreads across the whole fleet.
`max_concurrency` is only a hint bounding how many shards of the node are
dispatched per scheduling pass; authoritative capacity stays with the
leases. The node completes when every shard completes and fails as soon as
any shard fails terminally. Before a `reduce:` node is claimed, the server
aggregates its `from` node's shard outputs into `<node_key>.shards.json` in
the job directory. Fan-out beyond `max_shards` (default 1000) is rejected
when the shards are materialized.

**Built-in shard contract.** Pi/OpenClaw and remote workers receive the shard
index/input in their prompt and must write JSON to `shard_output.json` in the
working directory. Local handlers consume `context.runtime["shard_input"]`
directly and return `ExecutionResult.output_json`. The finish path persists
that value to `node_shards.output_json`; reduce receives its generated
`<node_key>.shards.json` as an ordinary declared input (artifact ref remotely).

## 9. Memory calibration (required before the 100-agent gate)

The 200 MB/slot budget is planning headroom from a 90-second sample (settled
~150 MB, peak ~187 MB, 2026-07-18). Long-run peak RSS over full-duration jobs
is not yet measured — calibrate before enabling 100-agent production:

1. Run full-duration production jobs on each device class (Mac mini and
   Raspberry Pi separately — ARM vs x86 memory profiles differ).
2. Record peak RSS of the `pi` processes:
   - sampled: `ps -o rss= -p <pid>` in a loop during the run;
   - per-process on macOS: `/usr/bin/time -l pi ...` reports maximum resident
     set size at exit.
3. Adjust per-device slot counts from the measured long-run peak, keeping OS
   headroom: `slots = floor((RAM - OS reserve) / measured peak)`.
4. Update `pi-remote` `global_capacity` in `config/workflow.yaml` to the sum of
   per-device slots and restart the server.

If long-run growth exceeds 200 MB/agent materially, reduce slot counts — do
not raise the budget silently (spec Risks).

## 10. Troubleshooting

| Symptom | Cause | Action |
| --- | --- | --- |
| `/api/remote/claim` returns 204 forever | No executions enqueued for the worker's capabilities | Check workspace node bindings (§8) and that `--capabilities` matches the executor's declared capability names exactly |
| Heartbeat 409 storms (`claim lost`) | Network partition or server restart — the claim lease expired (`claim_timeout_seconds`) and the execution was requeued/failed | Any 409 is terminal claim-loss: the worker kills the run immediately (only 5xx/network errors are retried). Persistent storms mean runs are dying — check tailnet, then rerun the failed jobs per existing semantics |
| Result upload 409 (`execution is not claimed by this worker`) | Same lease expiry as above, discovered at report time | Rerun the job; if frequent, raise `claim_timeout_seconds` or investigate tailnet stability |
| Gateway 502 | 中台 unreachable from the laptop (VPN dropped, SSID/network change) | Restore the laptop's company network path; workers' pi runs fail fast and retry with backoff |
| pi "model call failed" on workers | pi provider `base_url` wrong on the device | Re-check §5 step 3: must be `http://<laptop-tailnet-ip>:8788/v1` |
| Bundle fetch 410 (`bundle is no longer available`) | Server restarted mid-claim and the staged bundle was dropped | The execution is requeued automatically (bounded by `requeue_limit`); rerun if it exhausted |
| Result upload 413 | Archive exceeds `remote.max_archive_bytes` (default 64 MiB) | Investigate why artifacts ballooned; raise the limit only if legitimate |
| Worker-facing calls return 401 | Per-worker token is invalid or revoked | Restart with the correct management `--register-token` to issue a fresh per-worker token (§6, §7) |
| `/api/remote/claim` returns 409 protocol too old | Worker omitted `worker_version` or is below the configured minimum | Upgrade `scripts/remote/worker.py`; use minimum 0 only as a short emergency escape hatch |
| Server 503 on `/api/remote/*` | No remote executor configured / token missing | Server refused remote mode at startup — fix §6 and restart |
| Everything idle, nothing failing | Laptop asleep or offline | Workers hold no state and recover on their own; enforce §2 item 5 |

## 11. Security notes

- **Tailnet ACLs:** restrict device-to-device traffic so workers can reach only
  port 8000 (server API) and port 8788 (gateway) on the laptop. Nothing else on
  the laptop should be reachable from worker devices.
- **Gateway exposure:** binds the tailnet interface only; it is the single
  holder of the 中台 credential and must not be run with a widened bind
  address. It proxies only `POST /v1/*`.
- **Token handling:** `VIDEO_HIVE_REMOTE_WORKER_TOKEN` lives in the server's
  `.env` and in the workers' launch environment — never in `config/workflow.yaml`,
  command templates, or logs. It now doubles as the management token for the
  worker-token endpoints. Per-worker tokens are stored server-side as sha256
  hashes of the secret part only; the plaintext is shown once at issuance.
- **Revoking a worker:** when a device is lost or suspect, revoke it — the
  per-worker token stops authenticating immediately:

  ```bash
  curl -sS -X POST http://localhost:8000/api/remote/workers/<worker_id>/revoke \
    -H "X-Worker-Token: $VIDEO_HIVE_REMOTE_WORKER_TOKEN"
  ```

  Re-onboard later by re-issuing via `workers/register` (§6): it rotates the
  token and clears the revocation.
- **Worker hygiene:** no credentials, secret-bearing prompts, or API keys in
  worker logs; workers store no persistent state.
- **Worker labels:** labels travel in register/claim payloads and are listed
  by the page-facing `GET /api/remote/workers`. They are routing metadata —
  never put secrets, tokens, or other sensitive values into label keys or
  values.
- **Archive safety:** result archives are path-validated on both ends (no
  absolute paths, `..`, or links) before extraction.
- **Compliance:** precondition 1 (§2) is a hard blocker — encrypted transport
  is not policy approval.
