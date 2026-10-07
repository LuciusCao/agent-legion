# Remote Execution Runbook

Operator guide for running Agent Workers on remote devices (e.g. a home Mac
mini) with a primary machine as the only one that can reach the LLM provider.

> Distributed execution uses the **Agent Worker** protocol: Workers register,
> claim, heartbeat and report over `/api/agent-workers/*` and
> `/api/agent-executions/*`, with Docker Compose as the standard deployment.
> Container setup, secrets, registration and day-2 operations live in
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
**inside** the Worker container — Host API, gateway, and the object-storage
public endpoint (`AGENT_LEGION_S3_PUBLIC_ENDPOINT`), all by tailnet
address — per
[agent-worker-deployment.md §7](agent-worker-deployment.md#7-tailnet-冒烟验证上线前必须执行).
The storage endpoint is load-bearing: presigned GETs fetch materials and
bundle members, presigned PUTs return artifacts, and compose-internal names
(`seaweedfs:8333`) are unreachable from remote devices. With the bundled
object storage, before the smoke test publish the backend port beyond
loopback (`AGENT_LEGION_S3_BIND`: the tailnet address or `0.0.0.0` in the
Docker form, `0.0.0.0` in the native form) and point
`AGENT_LEGION_S3_PUBLIC_ENDPOINT` at the laptop's tailnet address; leave
`AGENT_LEGION_S3_ENDPOINT` on its local/compose address, otherwise the
local backend is treated as external and not started. The bind also
publishes the SeaweedFS master UI (`:9333`; RustFS console `:9001`), so a
`0.0.0.0` bind must be fenced with a host firewall or Tailnet ACL and never
exposed to the public internet; the narrower option is binding the
specific tailnet IP (native form: also point `AGENT_LEGION_S3_ENDPOINT` at
it and set `AGENT_LEGION_LOCAL_S3=always`). The rules live in
[materials-storage-deployment.md §1](materials-storage-deployment.md#1-组件与配置面).
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

Alternatively `make llm-gateway` reads the upstream provider credentials
from a Pi-format `models.json`; the path is machine-specific and must be
passed explicitly. The target binds `127.0.0.1` by default, so a gateway
meant for remote workers must also get `LLM_GATEWAY_HOST` (and the token
in the environment):
`LLM_GATEWAY_TOKEN=<random-shared-token> make llm-gateway PI_MODELS_JSON=~/.pi/agent/models.json LLM_GATEWAY_HOST=<laptop-tailnet-ip>`
(optionally `LLM_GATEWAY_PROVIDER` / `LLM_GATEWAY_PORT`). Both
`REMOTE_LLM_*` environment variables are required in the env-var form; the
gateway refuses to start without them. Do not inline real keys into shared
terminal history — export them from a local-only shell or a `.env` you
`source` first.

`LLM_GATEWAY_TOKEN` is the gateway's own access control: when set, every
request must present it as `X-Gateway-Token` or `Authorization: Bearer`. When
unset the gateway is open — acceptable only on loopback. **Binding a tailnet
(or any shared) interface without `LLM_GATEWAY_TOKEN` is a hard violation**:
anyone who can reach the port would spend the provider credential (the
gateway itself only logs a warning when the token is unset). On each
worker machine, provide the same token to the Worker container via
`deploy/.env` or the shell environment (`LLM_GATEWAY_TOKEN=...`; see the
deployment doc, §2) and point the gateway provider in the mounted velites
`models.json` at `http://<laptop-tailnet-ip>:8788/v1` with `apiKey:
"$LLM_GATEWAY_TOKEN"` — velites interpolates the variable and sends it as
`Authorization: Bearer`, which the gateway accepts (bare-metal pi works the
same way). `worker/execution/run.py::agent_subprocess_env` takes the token
from the worker environment and passes it to the agent subprocess; a value
in the worker config file is ignored.

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

Worker setup, registration tokens, the claim-off default, the code execution
pool and where the velites binary comes from are covered end-to-end by
[agent-worker-deployment.md](agent-worker-deployment.md) (the authority for
those topics). This section is the authority for the **protocol**: versions,
mixed-fleet compatibility and upgrade order.

- One Worker **container** per machine; an internal supervisor runs up to
  `max_concurrency` concurrent Agent executions plus up to
  `max_code_concurrency` code executions (two independent pools, accounted
  and enforced separately by the Host; code requests do not consume the
  workspace Agent cap).
- Registration uses workspace-scoped tokens; every registration presents all
  configured tokens and the Host resolves their union scope (rejecting the
  whole registration if any token is unknown or deleted). Issuing, importing
  and deleting keys:
  [agent-worker-deployment.md §4](agent-worker-deployment.md#4-worker-机器准备).
- Protocol: `register → claim → heartbeat → result` over
  `/api/agent-workers/register`, `/api/agent-executions/claim`,
  `/api/agent-executions/{id}/heartbeat` and `/api/agent-executions/{id}/result`.
  Registration carries `protocol_version` and `image_version`; the Host rejects
  workers below `agent_workers.min_protocol_version` (DB instance settings,
  `/api/admin/instance-settings`). Current protocol is **v5**: v2 added
  `kind: "code"` claims and heartbeat cancellation bodies; v3 adds
  runtime-scoped model declarations plus a `host_protocol_version`
  registration handshake (the Host reads an old Worker's bare provider/model
  declarations as runtime wildcards); v4 adds gzip-compressed artifact objects (v4+
  Workers receive `.gz`-suffixed upload specs and `content_encoding: gzip`
  input refs; older Workers keep bare keys); v5 adds the per-Worker batch
  heartbeat — `POST /api/agent-executions/heartbeats` renews every claimed
  lease of the machine in one write transaction, so heartbeat write traffic
  scales with machine count, not slot count. The single execution endpoint is
  unchanged and still serves older Workers. Code capacity only requires
  protocol ≥ v2.

  Two things decide whether a mixed fleet works, and the protocol number is
  only one of them. The first is the Worker-side handshake
  (`worker/host/client.py`): a v3+ Worker refuses to start unless the
  registration response's `host_protocol_version` is **at least its own**
  `PROTOCOL_VERSION` (`shared/protocol.py`; a pre-v3 Host omits the field,
  read as 0) and exits 2 before its first claim. The Host never rejects an
  older protocol (`min_protocol_version` is 1). The second is the set of
  wire changes that shipped **without** a protocol bump (recorded in the
  `shared/protocol.py` docstring): an old Worker whose protocol number the
  Host accepts can still be unable to run anything. Support is therefore
  stated per **Worker release** (`worker-v*` image tag; a locally built
  `agent-legion-worker:local` counts as the release its source revision
  belongs to), not per protocol number.

  Wire changes between Host and Worker that did not bump the protocol:

  | Change | Host side from | Worker side from | Older Worker × newer Host | Newer Worker × older Host |
  | --- | --- | --- | --- | --- |
  | #546 batch claim: the Worker sends `limit` and reads `{"claims": [...]}` | v0.7.4 | worker-v0.7.4 | works (no `limit` → single-object body, until v0.7.12) | works (the older Host ignores the batch fields; the Worker's shape sniff wraps the single object, `worker/host/claim_ops.py`) |
  | #547 single-object claim body removed: every non-empty claim answers `{"claims": [...]}` (`server/app/routes/agent_worker_claims.py`) | v0.7.12 | — | **broken** for Workers before worker-v0.7.4: they read the wrapper as one claim, so every execution they claim stays leased without starting and requeues only when the lease expires | n/a |
  | #657 concurrency ceiling 1024 → 2048 (`shared/concurrency_limits.py`; registration `max_concurrency` / `max_code_concurrency` and claim limits) | v0.7.12 (88f48f5cf) | worker-v0.7.12 | works (older Workers never declare more than 1024) | **rejected** when the Worker is configured above 1024: an older Host answers registration with 422 (and would reject such a claim body the same way); at ≤ 1024 it works |
  | #211 M3 `workflow_key` removed from claim responses | v0.7.16 | — | works for every `worker-v*` release (no Worker reads it since v0.5.0) | n/a |
  | #748 / #755 `X-Agent-Result` carries raw UTF-8; a direct-upload artifact list over the 14 KiB header budget moves into the result archive (`result-output-artifacts.json` + header flag) | v0.7.14 | worker-v0.7.14 | works (older Workers send ASCII-escaped JSON) | degraded: CJK `error_message` / `agent_stderr_tail` arrive as mojibake, and a run whose artifact list overflows the header fails with missing outputs |

  Additive fields (the batch heartbeat's `settled` list #590, the claim's
  `execution_generation` #759 and `max_archive_bytes` #959) are tolerated
  absent in both directions and are not breaking.

  Compatibility matrix by release (rows: Host release and its protocol;
  columns: Worker release; every `worker-v*` tag declares protocol v4 or v5):

  | Host \ Worker | worker-v0.6.0 – v0.6.1 (v4) | worker-v0.7.0 (v5) | worker-v0.7.4 – v0.7.13 (v5) | worker-v0.7.14 and later (v5) |
  | --- | --- | --- | --- | --- |
  | **v0.4.0-alpha and earlier** (≤ v3) | registration refused, exit 2 | registration refused, exit 2 | registration refused, exit 2 | registration refused, exit 2 |
  | **v0.5.0 – v0.6.0** (v4) | works (gzip artifacts, per-execution heartbeats) | registration refused, exit 2 | registration refused, exit 2 | registration refused, exit 2 |
  | **v0.7.0 – v0.7.11** (v5, single-object claims still served) | works (per-execution heartbeats) | works | works (batch claims from Host v0.7.4; per-claim fallback before); worker-v0.7.12+ configured above 1024 concurrency is rejected at registration (#657) | degraded (#748 / #755 row above); above 1024 concurrency rejected (#657) |
  | **v0.7.12 – v0.7.13** (v5) | **broken** (#547) | **broken** (#547) | works | degraded (#748 / #755 row above) |
  | **v0.7.14 and later** (v5, current) | **broken** (#547) | **broken** (#547) | works | works |

  **Minimum supported Worker for a current Host (v0.7.12 and later):
  worker-v0.7.4.** The recommended pairing is the Worker release of the
  same version as the Host. A locally built image counts as the release its
  source belongs to, whatever protocol number it declares: source from
  v0.5.0 on already declares v4, and any Worker built before the #546 batch
  claim (worker-v0.7.4) registers fine on a current Host but hits the same
  #547 claim break; source older than v0.5.0 (protocol ≤ v3) is likewise
  unsupported. `min_protocol_version` cannot express this floor —
  worker-v0.7.0 already declares v5 — so enforce it by upgrading Workers,
  not by raising the setting. The v5 Worker's per-execution heartbeat
  fallback (it degrades for good when the batch route answers 404/405,
  with a 5s per-beat timeout; transient errors do not trigger it) is not a
  mixed-fleet mode: it only covers a Host rolled back underneath an
  already-registered v5 Worker, which then exits 2 at its next
  registration (restart) if that Host is pre-v5.

  The Host's `min_protocol_version` remains 1; raising it is an emergency
  escape hatch, not part of a normal upgrade.
- **Upgrade order is Host first, then Workers** (same for pulled images:
  confirm the Host is healthy, then restart Workers one by one). A v3+ Worker
  treats a missing/older `host_protocol_version` as a terminal registration
  error and exits 2, so it cannot let an old Host erase model runtimes and
  misroute claims. Roll back Host and Workers together.
- **Result header (#748).** `X-Agent-Result` carries raw UTF-8 bytes (CJK
  error summaries are non-ASCII header values). Every reverse proxy / load
  balancer / gateway between Worker and Host must pass non-ASCII header
  values through unchanged — rewriting or rejecting them makes results
  undeliverable and the lease expires into a requeue. This wire change did
  not bump the protocol version, which is another reason for Host-first: a
  new Worker against an old Host (including a Host-only rollback) shows the
  CJK parts of `error_message` / `agent_stderr_tail` as mojibake (structure
  and success/failure verdicts are unaffected). The header has a 14 KiB
  budget; over budget the Worker sheds stderr tail → error_message →
  command → the artifact list, moving a direct-upload artifact list into the
  result archive (`result-output-artifacts.json`, #755) rather than
  dropping it. Result delivery verdicts (#959): the Worker prechecks the
  result archive against the claim-delivered `max_archive_bytes` and reports
  an oversized run failed instead of shipping it; a Host 4xx other than
  409 / 408 / 425 / 429 is degraded once into a failed report (no silent
  lease-expiry rerun), while 5xx / network errors and 408 / 425 / 429 keep
  retrying while the lease is held (§7).
- **`workflow_key` is gone from claim responses** (#211 M3): claims identify
  the workflow by `workspace_id` only. Every supported Worker (batch-claim
  era, #547) already ignores the field, so no Worker upgrade is required;
  the manifest's `workflow_key` (visible to node code) is unchanged.

**Capacity planning.** Budget RAM per concurrent Agent process from a
measurement of the runtime you actually deploy (sample peak RSS over
full-duration jobs, not a short window), keep OS headroom, and size
`max_concurrency = floor((RAM - OS reserve) / measured peak)`.

**Code execution on Workers.** Self-contained workflow code nodes are
dispatched to Workers with `max_code_concurrency > 0` and run inside the
`velites sandbox wrap` OS sandbox; the sandbox wrapper is baked into the
worker image (#383), so code capacity does not depend on the mounted velites.
When no online code-capable Worker exists, dispatch falls back to the local
Host executor. Setup, hot-update rules and velites placement (including the
#831 two-placement refresh on native `make prod-up`):
[agent-worker-deployment.md §5](agent-worker-deployment.md#5-启动-worker-机器上的-worker).

**Pure-remote mode (#389).** Setting the instance's `code_capacity` to 0
(admin 全局设置 → 本地执行, restart-effective) assembles **no** local
executor stack on the Host: no velites sandbox subprocesses, no thread pool,
no local heartbeat loop — code nodes execute 100% on remote code-capable
Workers. Pair it with a Worker fleet whose `max_code_concurrency` is sized
for your load. Without an online code Worker, code nodes queue silently (by
design); `/api/health` surfaces `execution_mode: pure_remote` plus the live
`online_code_workers` count, and the Host logs a WARNING at startup when the
count is 0. Shard executions follow the same rule: remote first, and in
pure-remote mode there is no local fallback at all.

Secret handling for code tasks is summarized in §8 (Worker hygiene).

## 6. Migrating an agent node between runtimes (pi ↔ velites)

`pi` and `velites` are peer runtimes declared per agent node: since 0.7.17
(#440 P3) every agent node carries its own execution profile in the workflow
revision (`execution.runtime`, or the workflow top-level default) — Agent
definitions are read-only history (their write API is deprecated). The
legacy capability resolution only serves job snapshots frozen before the v93
inlining and the nodes of an active revision that v93 could not inline: new
jobs on such a revision keep resolving those nodes by capability until the
node declares `execution.runtime` and a new revision is published. The yaml `agents:` section
and the `workflows.pi` block are retired (their presence in yaml fails Host
startup), and `workflows.pi.flavor` no longer exists: the node's
`execution.runtime` selects the adapter in the Host-side runtime catalog
(`server/app/agent_runtime/`), which pins the command builder (pi → pi argv,
velites → velites argv). openclaw was briefly a third runtime and retired
with #75 (no streaming events / token metering); new runtimes onboard via
the same adapter mechanism — see the onboarding guide in
`docs/architecture/velites-harness.md`.
Migrating one agent node to velites — or rolling it back — is a single-field
edit of the workflow plus a revision publish; no Host restart is required.
New jobs pick it up immediately; in-flight jobs keep their frozen snapshot
until they are upgraded (「升级 workflow」). Facts to know before flipping
the field:

- **Worker declarations first, definition migration second.** A queued request
  whose runtime no non-revoked Worker declares is failed by the unclaimable
  sweeper with an explicit runtime reason. Worker declarations are derived
  from binary detection (issue #254): at startup the Worker probes each
  supported runtime's binary (bundled `data/bin/<binary>` first, then PATH)
  and enables every detected one by default — installing the velites binary
  and restarting is all a fleet needs to start declaring `velites`; machines
  that must not take a runtime's jobs list it under `disabled_runtimes`
  (Worker console 配置 → Agent 运行时, or `workerctl configure
  --disable-runtime`). Since declaration follows detection, a fleet can no
  longer claim a runtime whose binary is missing — the mismatch class that
  used to strand claimed executions is gone.
- **Runtime-owned model discovery.** After binary preflight, the Worker runs
  each selected runtime's discovery adapter. For velites this is
  `velites models list --json` backed by `~/.velites/models.json`; only the
  intersection with the runtime-scoped Worker allowlist is registered. A
  provider/model absent from that registry is therefore never claimable.
- **Changing `runtime` publishes a new revision.** Already queued requests
  keep the profile they were enqueued with (`profile_source='node'` rows carry
  their runtime), so nothing is failed as stale; upgrade in-flight jobs when
  they should switch.
- **In-flight executions are unaffected.** Manifests are frozen at enqueue;
  claimed/running executions finish on the frozen command spec.
- **Rollback** is the same single-field operation: publish the workflow back
  with `execution.runtime: pi` (a workflow top-level default flips every agent
  node that does not override it).
- **Sandbox:** the `workflows.pi.velites_no_sandbox` escape hatch is retired
  with the yaml block; `execution.no_sandbox` is always false in manifests, so
  a sandbox incident currently requires a code change, not a config flip.
- **Execution defaults:** provider/model/thinking come from the workflow-level
  `execution:` default or per-node Studio overrides (strict chain, no
  workspace/global fallback) — runtime migration never touches them. Resolved
  values are validated against the runtime adapter's execution contract:
  a missing required key or a configured unsupported key fails fast at
  dispatch and at claim re-resolution.

  > **Status note:** the runtime migration described above is complete; the
  > canary playbook is kept as operational context for future runtime changes.

### 6.1 Self-contained agent nodes and rolling back to 0.7.15 (#933)

From 0.7.16 an agent node may declare its own profile in the workflow YAML
(`execution.runtime`, optional node-level `requires_labels`; the workflow
top-level `execution.runtime` is the default). Such a node needs no Agent
definition: its runtime is switched by editing the workflow and publishing a
revision, and in-flight jobs pick the change up only through「升级 workflow」
(the profile is frozen with the job snapshot). Queued requests of these
nodes are marked `profile_source='node'` in `agent_execution_requests`
(schema v92), with `agent_id` = node key.

**Rolling the Host back to 0.7.15** needs no schema step (v92 only added
columns, which 0.7.15 ignores), but plan for these rows:

- 0.7.15 does not understand `profile_source='node'`: its stale-definition
  sweeper finds no published Agent matching the row and fails the node with
  `Agent definition '<node_key>' was disabled or changed while the request
  was queued` (failure detail `stale_definition`). Nothing is claimed with
  the wrong profile.
- 0.7.15 ignores both fields when it loads the revision, so every
  self-contained node silently turns back into a legacy Agent node and needs
  exactly one published Agent definition for its capability. Publish those
  definitions (same runtime / labels as the node profile) before or right
  after the rollback, then rerun the failed nodes (rerun-by-failure on
  `stale_definition` works as for any stale request).
- Drain first when possible: pausing the affected workspaces and letting
  claimed executions finish avoids failing queued rows at all; claimed and
  running executions finish on their frozen manifests either way.

### 6.2 Agent profile backfill (schema v93) and rolling back to 0.7.16 (#935)

On upgrade, schema v93 inlines each legacy agent node of every workspace's
active revision and Studio draft from the published Agent definition it ran
(its materialized route target, else the capability's unique published
Agent): `execution.runtime`, `requires_labels`, `tools` (only when the node
had none), `config_schema` (overwritten) and `skill` (only when the node had
none). Before rewriting, the original text plus a per-node report lands in
`agent_profile_backfill_backups`; nodes that could not be resolved (no or
several published Agents, archived target, unportable skill) stay untouched,
are listed in the report's `report_json`, and block the next publish until
their profile is written on the node. Read the report after upgrading:

```sql
select workspace_id, source, report_json
from agent_profile_backfill_backups
where report_json like '%"unresolved"%';
```

From 0.7.17 publishing an Agent definition no longer changes what any
inlined node runs (D4): change the node profile and publish the workflow.

Differences from the 0.7.16 dry-run report (`scripts/agent_backfill_dry_run.py`,
#934) — v93 follows what dispatch actually runs, so a few nodes are
classified differently: an active-revision node whose route targets an
archived or unpublished Agent is *unresolved* in v93 (the dry-run backfilled
it from the archived definition); an active-revision node without a route
row resolves by its capability's unique published Agent in v93 (the dry-run
reported `no_route`). Both share `tools_empty_unportable` (an Agent published
with an empty tools list cannot be inlined into a node without tools — an
empty node list means the default tier) and `skill_unportable`.

Not backfilled: Studio chat session drafts (`studio_chat_sessions.draft_yaml`)
keep their legacy YAML, and pending publish requests
(`studio_publish_requests`) snapshot the draft they were raised on — after
the upgrade they fail with "Draft changed" (the workspace draft was
rewritten); raise the publish request again from the migrated draft.

**Rolling the Host back to 0.7.16** needs no schema step and has no down
migration: 0.7.16 reads the inlined fields as a self-contained profile (it
already supports them), so dispatch behaves the same. To restore the exact
pre-v93 text of a revision or draft, copy `original_text` (and, for a
revision, `original_hash` into `definition_hash`) from the backup table back
into `workflow_revisions` / `workspace_workflow_drafts.definition_yaml`.

## 7. Troubleshooting

| Symptom | Cause | Action |
| --- | --- | --- |
| Worker stays up but reports registration unavailable | Host unreachable or returning 5xx | The Worker retries registration in-process; verify `host_url` and the §3 smoke test, then inspect Host logs if 5xx persists |
| Worker becomes unhealthy with registration rejected | Registration token mismatch, or every key the Worker holds was deleted on the Host | `make stack-logs STACK=worker`; verify the configured keys still exist in the workspace settings and the Worker's registration status |
| Worker exits with code 2 and logs `启动预检失败` / startup preflight failure | A runtime listed in `AGENT_WORKER_EXPECT_RUNTIMES` (compose default `velites`) is not detected, its model discovery fails (e.g. wrong-architecture binary), or it is listed in `disabled_runtimes`; or `max_code_concurrency > 0` without a resolvable sandbox wrapper (`velites-sandbox` / `velites`) | Docker: place the arch-matched binary at `<repo>/velites-bin/velites` (remove the empty directory docker created there first) and restart. Bare metal: `./scripts/ensure-velites.sh --dest data/bin` (same OS/arch; a plain `cargo build --release` leaves the binary in `velites/target/`, not on PATH). Or drop the runtime from the expected set / re-enable it, or set `max_code_concurrency: 0`; details in [agent-worker-deployment.md §5](agent-worker-deployment.md#5-启动-worker-机器上的-worker) |
| Registration returns 401 | A scoped token is unknown or deleted on the Host (the Host rejects the whole registration when any token fails — deletion is the only lifecycle action, there is no revoke) | Issue a new key in the admin UI (workspace 设置 → Agent 与 Worker), add it in the Worker console (配置 → Workspace 访问), and delete the stale key — deletion cascade-cuts every Worker still bound to it |
| Registration returns 400 `unsupported Agent Worker protocol` | Worker's `protocol_version` below `agent_workers.min_protocol_version` | Rebuild the worker image from the current repo; lower the minimum only as a short emergency escape hatch |
| Claim returns 204 forever | No queued executions compatible with the worker's runtimes/labels | Check the workflow's Agent node routing and the worker's detected/enabled runtimes (配置 → Agent 运行时) plus `labels`; the Host-side `claim.empty` vs `claim.rejected` events (§7.1) distinguish a drained queue from an admission mismatch (reason code names the gate) |
| Heartbeat/result 409 (`execution is not owned by this Worker`) | Network partition or Host restart — the execution lease expired and was reassigned/failed | Terminal for that execution; rerun the job. Persistent storms mean the tailnet is unstable |
| Result upload 413, or node failed with `over the …-byte Host archive ceiling` / `result report rejected by Host: HTTP 413` | Archive exceeds `agent_workers.max_archive_bytes` (default 64 MiB). Since #959 the Worker prechecks the claim-delivered ceiling and reports the run failed instead of shipping the archive; a Host 413 (or any other 4xx verdict except 409 / 408 / 425 / 429) is degraded once into a failed report, so the execution is not silently rerun. Host 5xx / network errors are never degraded: the Worker keeps retrying while it holds the lease | Investigate why artifacts ballooned; raise the limit only if legitimate |
| Execution failed with `Agent bundle has more than … members` / `unpacks to more than … bytes` | The execution bundle exceeds the Worker's extraction limits (20,000 members / 1 GiB unpacked, #967) | Inspect the bound skill repository / node libs for accidentally committed bulk data |
| Agent model calls fail inside the worker container | Gateway unreachable or token rejected | Re-run the §3 container smoke test; confirm `LLM_GATEWAY_TOKEN` is set in `deploy/.env`, matches the gateway, and is referenced as `$LLM_GATEWAY_TOKEN` in the mounted velites `models.json` |
| Gateway 502 | LLM provider unreachable from the laptop (VPN dropped, network change) | Restore the laptop's network path to the provider; workers' agent runs fail fast and surface as failed executions |
| Gateway 401/403 | `LLM_GATEWAY_TOKEN` missing or mismatched | Gateway and every worker must share the same token (§4); never run a tailnet-bound gateway without it |
| Batched agent failures with `unexpected EOF during chunk size line` while other apps on the same machine also lose connectivity | Worker egress silently routed through a local proxy process (Clash/mihomo) inherited from the launch shell; the proxy's config reload/subscription refresh cuts every in-flight stream at once (#444) | The service strips inherited proxy env at startup (a one-line INFO log marks it). Production workers must not run behind a local proxy process; if egress through a proxy is genuinely required, declare it explicitly in the worker config (`proxy:` field / console 高级参数 → 出网代理) so the choice is visible and owned |
| Worker claims steadily but concurrency "breathes" below configured capacity during recovery | Success-path claim pacing (#472) is adaptive: the wait after a successful claim is the last claim round-trip × 0.5, clamped to [10ms, 100ms] (the pre-0.7.0 fixed 0.2s wait is gone); since #546 one round-trip claims a batch (`claim_batch_limit`, default 32, hot) and pacing tracks the batch's equivalent per-claim RTT (batch RTT ÷ batch size); an empty queue resets to the floor, error paths keep the #437 exponential backoff | Expected behavior — the floor is a deliberate guard for claim-transaction lock contention. If recovery throughput still matters, check `worker claim pacing <N>ms` log lines for the current band; a cold-start burst can additionally be shaped with `ramp_up` (deployment doc §5) |
| 高并发档位下运行容量规律性锯齿：贴满上限 → 数分钟一次掉 10%–20% 并一两分钟回满，worker 侧上传队列同时排队 | Host 单进程控制面在完成波下饱和（#521）：DAG 同相位节点成波报告，result commit 的 GIL 绑定工作（tar 解包、产物校验、写事务、events 后处理）打满单核，claim/心跳被饿死 | 运行画像（`/api/metrics/runtime-profile`）的 result 分段列（schema v80 起：`result_unpack / artifacts_verify / validate / artifacts_upload / lease_write / events / mark_done_seconds_total/max`）指认吃 CPU 的段；`result stages:` 日志行（超过 `AGENT_LEGION_SLOW_RESULT_MS`，默认 15s，升 WARNING）给单次分解。削峰 gate 默认已开（`agent_workers.max_concurrent_result_commits` = 16，instance settings 可调，0 = 关闭做 A/B）；gate 的排队等待是 result 总时长减去分段和的残差（spool 同在其中）——评估 gate 效果看这个数。调 gate 时注意连接池配比：events 段持读连接嵌套开写连接，gate 并发 × 2 逼近 `AGENT_LEGION_DB_POOL_MAX_SIZE`（默认 32）时 result 会在波峰 500（池超时），建议 gate ≤ pool/2；遗留绝对路径警告应已由一次性清理归零（启动报告 `report_absolute_db_paths` 全零），仍在刷说明有不可映射行留在库里 |
| Worker 容器内 `Z` 态（僵尸）bwrap/velites 进程随 executor 异常退出累积，PPid 全是 1；或 supervisor 日志成批出现 `discarded unverifiable agent pgid record` | executor 被 SIGKILL（如 OOM killer）后其沙箱子进程被收养给容器 PID 1（`worker.service`）。#682 前 PID 1 不收割孤儿，孤儿进程组清理又依赖精简镜像里没有的 `ps` | 自 #682 起：作为 PID 1 时 supervisor 每 5 秒扫 `/proc` 收割被收养的僵尸（只 wait 已证明不属于任何 `subprocess.Popen` 的 pid：executor 登记豁免，同会话子进程须连续两轮仍为僵尸，日志 `PID 1 已收割 N 个孤儿僵尸进程`）；孤儿进程组身份改读 `/proc/<pid>/cmdline`（无 `/proc` 的 macOS 仍走 `ps`），杀完立即收割（日志 `reaped orphaned agent process group <pgid> (collected N)`）。旧版本的存量僵尸重启容器即清 |
| Everything idle, nothing failing | Laptop asleep or offline | Workers recover on their own; enforce §2 item 5 |

### 7.1 Structured event codes (#490)

The Worker data plane emits single-line JSON lifecycle events on both sides
(`event` / `ts` + per-event payload). Host-side events go to stderr on the
`agent_legion.worker_events` logger (INFO for transitions, DEBUG for the
normal rhythm — enable debug when hunting); Worker-side events ride the
supervisor console stream AND persist to the structured-events sink
`data/logs/events-<state dir 名>.jsonl` (#510; 5MB×3 rotation — the 500-line
panel deque scrolls in 1–2 minutes under full load, the file keeps the
low-frequency exception events reachable for a whole busy episode). Align
the two sides by `execution_id` / `worker_id`.

| Event | Side | Meaning / key fields |
| --- | --- | --- |
| `worker.registered` | Host | Registration committed: runtime version matrix, concurrency declarations, resolved workspace scope |
| `worker.register_rejected` | Host | Registration refused (400/401): `reason` (`protocol_version_too_old` + `min_protocol_version`, `register_key_deleted`, `invalid_registration`) |
| `worker.offline` | Host | A previously-online worker crossed the `last_seen` threshold (30 s); `last_seen_at` (the DB-true last seen) + `threshold_seconds`; fires once per transition |
| `claim.granted` | Host | A claim succeeded: `runtime`, `model`, pool occupancy (`agent_active`/`code_active`); every claim is a batch claim since #547 retired the single path — one line per claimed execution, and the occupancy counters read the batch's FINAL pool state |
| `claim.empty` | Host | 204 — queue drained for this worker's pools; `reasons` when the queue head was skipped (paused workspace, lock races…) |
| `claim.rejected` | Host | Stock present but this worker was not admitted — see the reason codes below; when every pool is at its cap the scan never runs and the live pool state is the evidence (`capacity_full`/`code_capacity_full` synthesized from it) |
| `execution.started` | Host | Reserved name in the event namespace (the claim→run start is covered by `claim.granted` + Worker-side `execution.claimed`) |
| `execution.finished` | Host | Terminal commit: `outcome` (`completed`/`failed`/… or `rejected` with `reason: not_owned`), `exit_code`, `wall_seconds` (claim → committed result; `null` when the post-commit read failed), 携带 `agent_stderr_tail` 的结果（崩溃/超时）另带 `stderr_tail_excerpt`（保留 tail 的末 200 字符——tail 保尾是因崩溃栈在流末尾） — committed outcomes are DEBUG rhythm, `outcome=rejected` is INFO (the last Host-side clue of that execution) |
| `execution.heartbeat_rejected` | Host | Heartbeat refused: `reason: not_owned` or `lease_not_active` — the worker must stop beating. 完成态收尾（done/cancelled 执行的迟到心跳）不产生本事件：Host 在 beat 事务内分类后随 batch 响应的 `settled` 通道返回，Worker 静默摘除该租约（#590） |
| `execution.lease_expired` | Host | The sweeper deleted an expired lease: `attempt`, `requeue_limit` (will it rerun here?) |
| `deferring expired agent lease`（WARNING 日志行，非事件；`server.app.agent_broker.heartbeat_deferral`） | Host | #566 一期止血：claim 已越过 TTL 但 worker 控制面仍新鲜（claim 轮询在触活 `last_seen_at`）——执行面心跳饿死 ≠ worker 死亡，本次不删租约不重排，execution 续命等心跳恢复自愈；同一 execution 每跨一个 TTL 桶打一条。含义：该 worker 过载到心跳线程抢不到 GIL。心跳静默超过 2×TTL 后照常过期（此时才产生 `execution.lease_expired`）；它打断的是「过期 → 重排队 → 立即重 claim → 负载更高 → 再过期」的死亡螺旋放大器。盲区已于二期闭合：claim 循环虽只在领取预算为正时发 claim，但同循环的状态同步（`get_self`，每 `heartbeat_interval_seconds` 一拍）只要主循环存活就持续触活 `last_seen_at`；主循环整体饿死（executor 进程饱和）由 supervisor 进程内的租约心跳 relay（`worker/heartbeat_relay.py`）兜底——relay 按 executor 落的 `lease_snapshot.json` 发拍，同样触活 `last_seen_at`，让本延期在纯饱和场景也能生效 |
| `worker.lease_reclaim_burst`（WARNING 单行 JSON，`server.app.agent_broker.lease_reclaim_audit`，默认级别可见） | Host | #681：一次清扫里同一 worker 被收回的租约数达到阈值（10）时按 worker 打一条，不再只散落在逐条 `execution.lease_expired` 里：`reclaimed`（本次收回数）及其按实际分支的拆分：`requeued`（重排待重跑）、`requeue_limit_exceeded`（当前代次且超过重排上限、直接判败）、`cancelled`（旧代次请求被代次重置取消，或节点已终态）；`deferred`（同一 worker 本次因控制面新鲜被 #566 延期的数）、`worker_last_seen_at`（该 worker 最后一次任意已认证交互）、`sample_execution_ids`（抽样，便于 grep 对齐）。`worker_last_seen_at` 与收回时刻同样陈旧 = 整机失联（executor 被杀/网络断）；之后若该 worker 在线却长时间无 `claim.granted`，先查 worker 侧 `claim_enabled`（控制台「在线·未领取」） |
| `execution.result_rejected`（WARNING 单行 JSON，同上 logger） | Host | #681：终态结果上报因租约不再归属而被 409 拒收——Worker 拿到 409 即丢弃该结果，这一行是 Host 侧唯一留痕：`stage`（`precheck` 落盘前预检 / `commit` / `finish` / `mark_done`）、`reason`（读请求行判定：`requeued` 已被清扫收回待重领、`reassigned` 已被其他 worker 领走、`superseded` 本 worker 以新租约重领、`lease_not_active`、`request_done` 等终态、`missing`）、`status`/`exit_code`、`carries_artifacts` + `output_artifact_count`、`archive_bytes`。成片出现 = 收回后迟到的已完成结果被丢弃，与同时段的 `worker.lease_reclaim_burst` 对照 |
| 心跳 relay 日志行（`worker/heartbeat_relay.py`，进 worker 控制台与滚动日志 `data/logs/executor-<state dir 名>.log`） | Worker | 「租约停拍（Host 硬兜底回收）；控制面 ping 继续」= executor 主循环超 60s 未刷新租约快照（进程卡死级饱和）：relay 停续租约（过期后由 Host 2×TTL 硬兜底回收），但继续用不续租的轻量已认证 ping（get_self → record_seen）维持 `last_seen_at` 新鲜——Host 侧 #570 deferral 因此仍能区分「执行面饥饿」与「worker 真离线」；「心跳 relay 批量拍失败」= Host 不可达，逐拍重试；「控制面 ping 失败」= 停拍期 ping 异常（每 episode 一条）。排查卡点看 executor 滚动日志（10MB×5 轮转） |
| relay 存活看门狗日志行（`worker/relay_sync.py`，executor stdout） | Worker | 「心跳 relay 超过 Ns 无新拍（seq=…）」= relay 每拍重写 `lease_beat_result.json` 的 seq 即存活证明；executor 持有租约而 seq 停跳超阈值（3×relay 拍间隔、下限 60s）= relay 死亡/supervisor 挂起，租约将静默过期重排，查 supervisor 进程。纯观测信号，不改结果语义；每个停滞 episode 一条，seq 恢复后重置 |
| `claim.attempt` | Worker | One claim poll's local budget snapshot (`agent_budget`/`code_budget`/`upload_backlog`/`claim_enabled`); `limit` (#546) is the batch size this poll asks for |
| `claim.backoff` | Worker | The #437 backoff sequence's position: `failures`, `wait_seconds`, `error` |
| `execution.claimed` | Worker | A claim arrived — the Worker-side view of the Host's `claim.granted` |
| `execution.completed` | Worker | Local process/code exit: `exit_code`, `wall_seconds`; Host acceptance is its `execution.finished` |
| `execution.reported` | Worker | Upload pipeline delivery verdict (#551): per-stage wall times `queue_wait_seconds`（submit→lane）/ `prepare_seconds` / `transfer_seconds` / `report_wait_seconds`（report 车道排队）/ `report_seconds`（含重试退避）+ `outcome`（`delivered` / `rejected`（含租约 409——重复执行的指纹）/ `aborted`（关停或车道异常，marker 保留重投））+ `archive_bytes` |
| `execution.failed` | Worker | Local containment boundary fired (download/spawn/wait raised): `error` summary |
| `http.error` | Worker | Upstream error response (`status_code` + `url` + bounded `body`) or transport failure (`url` + `error`) — the middle-502 blind spot, since the Host never sees the response |

Batch claim (#546) note: a batch's skip reasons surface only on the zero-claim verdict (claim.empty above); a partially filled batch discards them (the claims themselves are the evidence). The batch-only skip `batch_lock_order` (candidates deferred to keep the batch transaction's workspace locks ascending) therefore never appears in an event — it is aggregated into the `skipped` count of the `claim stages:` log lines, and a batch that underfills against `limit` with a nonzero `skipped` is the signature.

`claim.rejected` reason codes (claim-path decision-point naming):

| Reason code | Meaning |
| --- | --- |
| `capacity_full` / `code_capacity_full` / `capacity_raced` | 并发池满 — the agent/code pool is at its declared cap (or lost the last-slot race) |
| `runtime_mismatch` | The definition's runtime is not in this worker's declared runtimes |
| `model_mismatch` | model 未声明 — the required provider/model is not in this worker's model declarations |
| `workspace_not_allowed` | scope 拒绝 — the request's workspace is outside this worker's admission scope |

Direct mappings for the common complaints: 「并发下来了」→ check the
`claim.rejected` reason distribution; 「本机拿不到任务」→ `claim.empty`
vs `claim.rejected` distinguishes drained queue from admission mismatch;
「worker 静默」→ `worker.offline` names the moment; 「502 类中间层错误」→
the Worker-side `http.error` carries the code and the target URL.

Supply→consume chain triage (#551/#552): one row per suspected stage, each
with its direct evidence — no more inferring from marker files.

| 现象 | 先看哪里 | 判读 |
| --- | --- | --- |
| 容量爬升慢 | Host `/api/metrics/runtime-profile` 的 `claim_queue_wait_seconds_*`（queued_at→promote）与 `enqueue_pending` / `enqueue_stock_gated` | queue_wait 高 + enqueue_pending 低 = 消费侧（worker 不足/爬坡钳制）；enqueue_pending 高 = 供给侧（备货池/备货门）；两者都低而并发低 = worker 容量或爬坡 |
| 上传积压（worker 控制台 queued 涨） | worker 日志的 `execution.reported` 分段：`queue_wait`（排队）/ `prepare`（归档 CPU）/ `transfer`（传输）/ `report_wait`（report 车道排队）/ `report`（Host commit RTT，含退避） | `report_seconds` 大 = Host result commit 慢（0.7.5 起解包已下沉进程池，#552；仍慢则看 Host 的 result 分段列）；`queue_wait` 大 = 上传并发不足（`upload_max_concurrency`）；`transfer` 大 = 链路/S3 |
| 结果延迟大、租约濒临 90s | `execution.reported` 的 `outcome=rejected`（409 = 租约已被重发，重复执行的指纹）+ Host `result_*` 分段列 | rejected 成片出现 = 上传链比租约 TTL 慢，先按上一行定位分段 |
| Host 进程单核贴顶 | `result_unpack_seconds_*` 分段（#552 后只剩进程池排队墙钟）+ 机器级采样 | unpack 段墙钟高而 Host CPU 低 = 进程池排队（admin 实例设置 `result_unpack.workers` 调大，重启生效；env `AGENT_LEGION_RESULT_UNPACK_WORKERS` 为覆盖通道，默认 min(4, 核数)）；unpack 低而总时长高 = 查其余分段 |
| validate 段墙钟高 | Host `result stages:` 的 `validate=` 分段 | #569 起物化按 (skill, commit) 共享缓存（命中零 git 调用）且校验下沉独立进程池；仍高 = validate 池排队（admin 实例设置 `result_validate.workers` 调大，重启生效；env `AGENT_LEGION_RESULT_VALIDATE_WORKERS` 为覆盖通道，默认 min(4, 核数)）或校验器本身慢（velites 契约引擎与业务规则脚本 `validate_output.py` 各 30s timeout） |

## 8. Security notes

- **Tailnet ACLs:** restrict device-to-device traffic so workers can reach only
  port 8000 (Host API), port 8788 (gateway), and the object-storage public
  endpoint (`AGENT_LEGION_S3_PUBLIC_ENDPOINT`, e.g. port 8333 with the default
  SeaweedFS backend; port 9000 only for the RustFS escape hatch) on the laptop —
  presigned GETs fetch materials/bundle members and presigned PUTs upload
  artifact staging. Nothing else on
  the laptop should be reachable from worker devices.
- **Gateway exposure:** binds the tailnet interface only; it is the single
  holder of the provider credential and must not be run with a widened bind
  address. It proxies only `POST /v1/*`. `LLM_GATEWAY_TOKEN` is mandatory for
  any non-loopback bind (§4). The token reaches workers only via
  `deploy/.env`/environment passthrough and is referenced from the worker's
  velites `models.json` as `"$LLM_GATEWAY_TOKEN"` — never as a literal in
  `models.json`, Compose YAML, or on a command line.
- **Registration token handling:** registration uses workspace-scoped tokens
  (issue #35): issue them per workspace in the Host Web UI
  （workspace 设置 → Agent 与 Worker） and add them on each worker machine via
  the Worker console or `workerctl configure --register-token-file` (fed via
  stdin inside a container, see the deployment doc §5) — never in
  `config/*.yaml`, worker YAML, images (`.dockerignore` excludes `**/secrets`
  and `**/.env`), or logs. The former global
  `AGENT_LEGION_WORKER_REGISTER_TOKEN`（or `_FILE`）env vars are retired and
  **fail startup** when set.
- **Worker hygiene:** no credentials, secret-bearing prompts, or API keys in
  worker logs; the Worker workdir volume holds only transient execution data
  and may be cleaned per retention policy. Code executions receive
  vault-resolved node secrets over the claim response; they are held in memory
  only and fed to the child via stdin — the Host-side
  `split_manifest_config` keeps secret-marked keys out of the dispatch
  manifest before it ever reaches the Worker, and nothing config-derived is
  persisted on the Worker side, so a secret value must never appear in the
  workdir volume or logs (tested by `test_secrets_stay_off_disk`).
- **Worker labels:** labels travel in the register payload and are listed by
  `GET /api/agent-workers`. They are routing metadata — never put secrets,
  tokens, or other sensitive values into label keys or values.
- **Bundle/archive safety:** execution bundles and result archives are
  path-validated on both ends (no absolute paths, `..`, or links) before
  extraction. With the presigned channel enabled, the result archive no
  longer embeds artifacts (`worker/upload/queue.py`).
- **Policy:** precondition 1 (§2) is a hard blocker — encrypted transport is
  not policy approval.

## 9. External artifact access

The external read path (job status, artifact manifest, direct presigned
download vs the raw endpoint, and the full copy-paste submit → poll →
download scripts) lives in
[workspace-api-tokens.md「读取产物」](workspace-api-tokens.md#读取产物清单直连下载与全链路示例).
