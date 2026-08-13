# Remote Execution Runbook

Operator guide for running Agent Workers on remote devices (e.g. a home Mac
mini) with a primary machine as the only one that can reach the LLM provider.

> **2026-07-21 update.** The old remote executor (`kind: remote` executors, the
> remote broker, `/api/remote/*` routes and `scripts/remote/worker.py`) was
> removed by the agent-definition-worker-routing change. Distributed execution
> now uses the **Agent Worker** protocol: Workers register, claim, heartbeat
> and report over `/api/agent-workers/*` and `/api/agent-executions/*`, with
> Docker Compose as the standard deployment. Container setup, secrets,
> registration and day-2 operations live in
> [agent-worker-deployment.md](agent-worker-deployment.md) — this runbook keeps
> only the cross-machine networking and LLM-gateway operations that document
> does not repeat.

## 1. Overview

The primary machine (called *the laptop* below) runs the Host (FastAPI +
PostgreSQL + workflow scheduling) and its own local Worker; remote devices run
one Worker container each. All LLM traffic flows **worker → tailnet → laptop
gateway → LLM provider**; the provider credential is injected by the gateway on
the laptop and never leaves it. Workers hold no secrets beyond their
registration token and the optional gateway token.

**Scope note.** Two components in this runbook are workarounds for a specific
deployment topology, not architectural requirements:

- the **LLM gateway** — needed solely because the LLM provider is reachable
  only from the laptop's network (disappears entirely with per-worker BYO
  models);
- the **tailnet** — needed solely because the laptop and remote devices are
  behind separate NATs (replaced by plain TLS + worker token if the control
  plane is ever publicly reachable).

## 2. Prerequisites checklist

Verify all five preconditions **before** any rollout step:

1. **Policy sign-off** — prompts and model responses physically transit remote
   devices outside the provider's network. Transport is WireGuard-encrypted,
   but encrypted transport is not policy approval. Hard blocker; confirm first.
2. **Tailscale installable on the laptop** (fallback: a cloud VPS running frp
   or Headscale as relay — see §3).
3. **The LLM provider exposes an OpenAI-compatible HTTP API** so the gateway
   can proxy it without protocol translation.
4. **The LLM provider tolerates ~100 concurrent requests from a single
   token/IP** — confirm with the provider; the design does not solve rate
   limits.
5. **Laptop stays awake during production runs** — `caffeinate -dims`, or AC
   power with display/system sleep disabled and no lid-close sleep.

## 3. Networking (Tailscale)

Tailscale is managed by the **host OS** on every machine; it is never embedded
in the business containers. Install Tailscale on the laptop and each worker
device and bring them up:

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
relays — latency rises from ~10–30 ms to a few hundred ms). If P2P proves
unstable, switch the relay layer to a cloud VPS running Headscale + DERP or frp
tunnels; every other component in this runbook is transport-agnostic and
unchanged.

The laptop's tailnet IPv4 address (`tailscale ip -4`, a `100.x.y.z` address) is
`<laptop-tailnet-ip>` in every command below. Expose the Host API on that
address via `AGENT_LEGION_HOST_BIND` (see the deployment doc, §2).

**Container caveat.** A Docker Desktop network namespace does not necessarily
inherit the host's Tailnet routes. Before going live, run the smoke test from
**inside** the Worker container — Host API and gateway, both by tailnet
address — per
[agent-worker-deployment.md §7](agent-worker-deployment.md#7-tailnet-冒烟验证上线前必须执行).
If the container cannot reach the tailnet, design a dedicated Tailscale
sidecar; do not bake Tailscale into the Worker image.

## 4. LLM gateway on the laptop

The gateway is a separate infrastructure process, outside the Host/Worker
pair. It binds the tailnet interface only, accepts `POST /v1/*`, and injects
the provider `Authorization: Bearer` header. Start it on the laptop from the
repo:

```bash
REMOTE_LLM_UPSTREAM="https://<provider-base-url>" REMOTE_LLM_KEY="<provider-key>" \
LLM_GATEWAY_TOKEN="<random-shared-token>" \
  uv run python scripts/remote/llm_gateway.py --host <laptop-tailnet-ip> --port 8788
```

Alternatively `make llm-gateway` reads the provider credentials from the local
Pi `models.json` (`LLM_GATEWAY_PROVIDER` / `PI_MODELS_JSON`). Both
`REMOTE_LLM_*` environment variables are required in the env-var form; the
gateway refuses to start without them. Do not inline real keys into shared
terminal history — export them from a local-only shell or a `.env` you
`source` first.

`LLM_GATEWAY_TOKEN` is the gateway's own access control: when set, every
request must present it as `X-Gateway-Token` or `Authorization: Bearer`. When
unset the gateway is open — acceptable only on loopback. **Binding a tailnet
(or any shared) interface without `LLM_GATEWAY_TOKEN` is a hard violation**:
anyone who can reach the port would spend the provider credential. On each
worker machine, provide the same token to the Worker container via
`deploy/.env` or the shell environment (`LLM_GATEWAY_TOKEN=...`; see the
deployment doc, §2) and set the pi provider's `apiKey` to
`"$LLM_GATEWAY_TOKEN"` in the mounted `models.json` — the pi CLI interpolates
the variable and sends it as `Authorization: Bearer`, which the gateway
accepts. `worker/executor.py` passes the variable through to the pi subprocess
environment unchanged.

Verify from a worker device (host OS first, then from inside the container per
§3):

```bash
curl -X POST http://<laptop-tailnet-ip>:8788/v1/chat/completions \
  -H "Authorization: Bearer $LLM_GATEWAY_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"ping"}],"stream":false}'
```

Expect a normal OpenAI-compatible chat completion response. A `502` means the
laptop could not reach the LLM provider (see §7).

## 5. Workers

Worker setup, registration tokens, Compose stacks and verification are covered
end-to-end by [agent-worker-deployment.md](agent-worker-deployment.md). The
essentials, for orientation:

- One Worker **container** per machine; an internal supervisor runs up to
  `max_concurrency` concurrent Agent executions.
- The Worker registers with the Host using a registration token delivered as a
  **secret file** (`AGENT_LEGION_WORKER_REGISTER_TOKEN_FILE` on the Host,
  `/run/secrets/agent_worker_register_token` in the container) — never in the
  image, Compose YAML or command line — and receives a per-worker token in
  exchange. Per-worker tokens are stored server-side as sha256 hashes only.
- Protocol: `register → claim → heartbeat → result` over
  `/api/agent-workers/register`, `/api/agent-executions/claim`,
  `/api/agent-executions/{id}/heartbeat` and `/api/agent-executions/{id}/result`.
  Registration carries `protocol_version` and `image_version`; the Host rejects
  workers below `agent_workers.min_protocol_version` (DB instance settings,
  `/api/admin/instance-settings`). Current protocol is **v2**: it adds
  `kind: "code"` claims (see below) and heartbeat response bodies carrying
  explicit cancellation lists. Compatibility matrix:

  | Host \ Worker | v1 Worker | v2 Worker |
  | --- | --- | --- |
  | **v1 Host** | agent-only (unchanged) | agent-only — the v2 Worker degrades gracefully (extra register/claim fields ignored, heartbeat 204 accepted) |
  | **v2 Host** | agent-only — never receives code claims, heartbeat stays an empty 204 | full — agent + code pools, heartbeat 200 + cancellation body |

  The Host's `min_protocol_version` remains 1; raising it is an emergency
  escape hatch, not part of a normal upgrade.
- Concurrency is bounded in two independent pools per Worker: Agent executions
  by the workflow's workspace-level `max_concurrency` and the Worker's local
  `max_concurrency`; code executions by the Worker's local
  `max_code_concurrency` only (code requests do not consume workspace Agent
  capacity). The Host accounts and enforces the two pools separately, so long
  code tasks never starve Agent claims. Upgrade order is Host first, then
  Workers.

**Capacity planning.** Budget ~200 MB RAM per concurrent Agent process
(measured pi RSS: settled ~150 MB, peak ~187 MB, 90-second sample). Long-run
peak RSS is still unmeasured — calibrate with full-duration jobs before
raising `max_concurrency`, and keep OS headroom:
`max_concurrency = floor((RAM - OS reserve) / measured peak)`.

**Code execution pool (protocol v2).** Self-contained workflow code nodes
(the six question/comprehension nodes and `video_package`; the three
`server.app.pipeline`-dependent video nodes stay Host-local) can be dispatched
to Workers: the Host ships the node code text plus a sha256 `code_hash` and a
`workspace_libs` snapshot in the bundle, and the Worker executes it inside the
same `velites sandbox wrap` OS sandbox used for custom nodes. To opt a Worker
in:

- declare the accepted code capabilities in the same `capabilities` list as
  Agent capabilities (no separate field; the Host matches by capability), and
- set `max_code_concurrency > 0` (0 = never receives code claims, the
  default). This field is **deliberately not hot-reloaded**: changing it via
  the console / `PUT /api/config` restarts the execution process, so the
  startup preflight re-runs — and refuses to start (exit code 2) when code
  capacity is declared without a `velites` binary on PATH. Never bypass this
  by editing the state-copy YAML under a running process.

When no online code-capable Worker exists, dispatch falls back to the local
Host executor — code tasks never rot in a queue waiting for a Worker.

**Secret boundary for code tasks.** Node secrets (vault-resolved connection
credentials) are injected into the claim response only — queued manifests and
bundles are stored secret-free. The Worker holds them in memory only, passes
them to the sandboxed child via stdin, and scrubs them before any persistence;
they never touch the Worker filesystem or logs.

## 6. Migrating an Agent between runtimes (pi ↔ velites)

`pi`, `openclaw` and `velites` are peer runtimes declared per Agent definition.
Definitions live in the `versioned_entities` table and are managed in Studio
(「Agent 管理」) or via `/api/agent-definitions` — the yaml `agents:` section
and the `workflows.pi` block are retired (their presence in yaml fails Host
startup), and `workflows.pi.flavor` no longer exists: `AgentDefinition.runtime`
pins the command builder directly (pi → pi argv, velites → velites argv).
Migrating one agent to velites — or rolling it back — is a single-field edit
plus publish; no Host restart is required (the published-catalog cache has a
~5s TTL, and the claim path re-resolves per request). Facts to know before
flipping the field:

- **Worker declarations first, definition migration second.** A queued request
  whose runtime no non-revoked Worker declares is failed by the unclaimable
  sweeper with an explicit runtime reason. Declare `velites` in the Worker
  fleet (`runtimes` in the Worker console/config) first; the Worker startup
  preflight refuses to start (exit code 2) when a declared runtime's binary is
  missing from PATH, so a fleet that claims velites without the binary fails
  loudly at boot instead of stranding claimed executions.
- **Changing `runtime` changes `definition_hash`.** Queued requests pinned to
  the old hash are failed as stale by the stale-definition sweeper. Migrate
  off-peak with the queue drained; re-submit staled jobs under the normal
  stale semantics.
- **In-flight executions are unaffected.** Manifests are frozen at enqueue;
  claimed/running executions finish on the frozen command spec.
- **Rollback** is the same single-field operation: publish the definition back
  with `runtime: pi`. A fleet-wide velites incident means migrating every
  definition back to `runtime: pi`.
- **Sandbox:** the `workflows.pi.velites_no_sandbox` escape hatch is retired
  with the yaml block; `execution.no_sandbox` is always false in manifests, so
  a sandbox incident currently requires a code change, not a config flip.
- **Execution defaults:** provider/model/thinking come from the workspace
  Settings「Agent 默认配置」or per-node Studio overrides (strict chain, no
  global fallback) — runtime migration never touches them.

  > **Status note:** the runtime migration is complete. The 5 question-chain
  > agents were migrated first; the 4 video-chain agents followed with the
  > schema v27 cutover (2026-08-05), which re-published every published
  > `runtime: pi` definition as `runtime: velites` and archived the old
  > versions. The canary playbook above is kept as historical context.

## 7. Troubleshooting

| Symptom | Cause | Action |
| --- | --- | --- |
| Worker stays up but reports registration unavailable | Host unreachable or returning 5xx | The Worker retries registration in-process; verify `host_url` and the §3 smoke test, then inspect Host logs if 5xx persists |
| Worker becomes unhealthy with registration rejected | Registration token mismatch or Worker revoked | `make stack-logs STACK=worker`; verify the token file and registration status |
| Worker exits with code 2 and logs `启动预检失败` / startup preflight failure | A declared runtime's binary is not on PATH (e.g. `velites` declared but not installed), or `max_code_concurrency > 0` without `velites` on PATH | Install the binary (`cargo build --release` in `velites/`, on PATH), drop the runtime from the Worker's `runtimes`, or set `max_code_concurrency: 0`, then restart |
| Registration returns 401 | `AGENT_LEGION_WORKER_REGISTER_TOKEN(_FILE)` on the Host does not match the worker's token file | Re-copy `deploy/secrets/agent_worker_register_token` to the worker machine (deployment doc §4) |
| Registration returns 400 `unsupported Agent Worker protocol` | Worker's `protocol_version` below `agent_workers.min_protocol_version` | Rebuild the worker image from the current repo; lower the minimum only as a short emergency escape hatch |
| Claim returns 204 forever | No queued executions compatible with the worker's runtimes/labels | Check the workflow's Agent node routing and the worker's declared `runtimes` / `labels` |
| Heartbeat/result 409 (`execution is not owned by this Worker`) | Network partition or Host restart — the execution lease expired and was reassigned/failed | Terminal for that execution; rerun the job. Persistent storms mean the tailnet is unstable |
| Result upload 413 | Archive exceeds `agent_workers.max_archive_bytes` (default 64 MiB) | Investigate why artifacts ballooned; raise the limit only if legitimate |
| pi "model call failed" inside the worker container | Gateway unreachable or token rejected | Re-run the §3 container smoke test; confirm `LLM_GATEWAY_TOKEN` is set in `deploy/.env` and matches the gateway |
| Gateway 502 | LLM provider unreachable from the laptop (VPN dropped, network change) | Restore the laptop's network path to the provider; workers' pi runs fail fast and surface as failed executions |
| Gateway 401/403 | `LLM_GATEWAY_TOKEN` missing or mismatched | Gateway and every worker must share the same token (§4); never run a tailnet-bound gateway without it |
| Everything idle, nothing failing | Laptop asleep or offline | Workers recover on their own; enforce §2 item 5 |

## 8. Security notes

- **Tailnet ACLs:** restrict device-to-device traffic so workers can reach only
  port 8000 (Host API) and port 8788 (gateway) on the laptop. Nothing else on
  the laptop should be reachable from worker devices.
- **Gateway exposure:** binds the tailnet interface only; it is the single
  holder of the provider credential and must not be run with a widened bind
  address. It proxies only `POST /v1/*`. `LLM_GATEWAY_TOKEN` is mandatory for
  any non-loopback bind (§4). The token reaches workers only via
  `deploy/.env`/environment passthrough and is referenced from the worker pi
  provider config as `"$LLM_GATEWAY_TOKEN"` — never as a literal in
  `models.json`, Compose YAML, or on a command line.
- **Registration token handling:** `AGENT_LEGION_WORKER_REGISTER_TOKEN` (or the
  `_FILE` variant) lives in the Host's environment/secret mount and in
  `deploy/secrets/` on each worker machine — never in `config/*.yaml`, worker
  YAML, images (`.dockerignore` excludes `**/secrets` and `**/.env`), or logs.
- **Worker hygiene:** no credentials, secret-bearing prompts, or API keys in
  worker logs; the Worker workdir volume holds only transient execution data
  and may be cleaned per retention policy. Code executions receive
  vault-resolved node secrets over the claim response; they are held in memory
  only, fed to the child via stdin, and scrubbed before any persistence — a
  secret value must never appear in the workdir volume or logs (enforced by
  `strip_secret_config`, tested by `test_secrets_stay_off_disk`).
- **Worker labels:** labels travel in the register payload and are listed by
  `GET /api/agent-workers`. They are routing metadata — never put secrets,
  tokens, or other sensitive values into label keys or values.
- **Bundle/archive safety:** execution bundles and result archives are
  path-validated on both ends (no absolute paths, `..`, or links) before
  extraction.
- **Policy:** precondition 1 (§2) is a hard blocker — encrypted transport is
  not policy approval.
