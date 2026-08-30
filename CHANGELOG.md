# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/) once 1.0.0 is released.

## [Unreleased]

### Added
- 架构盘点：workflow_key 退役 Phase 1 分类清单（`docs/architecture/workflow-key-retirement-inventory.md`，issue #211）——四类穷尽引用 + Phase 2-4 执行依据。

## [0.4.0-alpha] - 2026-08-29

### Added

- `make install`: one-command setup for fresh clones — detects and (on macOS)
  installs missing prerequisites (uv, Python 3.11+, Node 18+, PostgreSQL 17,
  cargo, Docker), then runs `uv sync`, creates the database, generates `.env`
  with random local-RustFS credentials, builds the velites sandbox binary,
  installs frontend dependencies, and seeds the worker config and vault key
  (`scripts/install-deps.sh`, idempotent).
- Dev object storage works out of the box: `make dev-up` now starts the local
  RustFS container (via the existing `materials-local` compose profile, gated
  by `local-s3-decide.sh`) and ensures the bucket + CORS exist
  (`scripts/ensure-s3-bucket.py`, shared with `init-worktree.sh`). Switching to
  a cloud S3 is still just an `.env` edit — the local RustFS is then skipped
  automatically.
- Workflow definitions accept an optional top-level `execution:` block
  (provider/model/thinking) that the loader merges into every non-start
  node (node values win), versioned with the revision — one place to configure
  execution per workflow instead of per node.
- Studio node execution editor: provider/model inputs now offer runtime-aware
  suggestions aggregated from the workspace's online workers
  (`GET /api/workspaces/{id}/runtime-models`), with free-text fallback.
- Studio chat sessions can be resumed after close/error/backend restart:
  `POST /api/workspaces/{id}/studio-chat/sessions/{sid}/resume` respawns the
  ACP runtime with a fresh scoped token and rebuilds context via ACP
  `session/load` when advertised, otherwise by replaying a bounded transcript
  of the persisted history into the first prompt. The panel offers a
  「继续对话」 action and remembers the last selected session per workspace.
- Studio start-node contract editor rewritten in user-facing terms: each
  accepted item type (上传文件 / 外部平台内容 / 整个文件夹) carries a label
  plus a one-line scenario description, shared with the read-only view and the
  AddItemsDialog banner (internal jargon like `accepted_item_types` removed).

### Removed (workspace settings retirement)

- Workspace Settings「Agent 默认配置」(`default_agent_provider/model/thinking`):
  the provider/model/thinking resolution chain is now node `execution.*` →
  workflow-level `execution` default → actionable error; the three columns are
  dropped in schema v64 (cleanup-phase drop after the v62 replay, per the
  `cms_config_json` precedent). New manifests no longer bake
  `execution_defaults`; claim re-resolution stays tolerant of legacy in-flight
  manifests.
- Workspace Settings「接入与资源」 intake-mode toggles: item types are
  declared solely by the start node's `accepted_item_types` in Studio; the
  legacy `/job-batches` API is no longer gated by enabled intake modes. The
  default entity type (entityType) survives and moved into「基础信息」.

### Removed (dead code and stale artifacts)

- Removed the dead `server/app/services/vault_resources.py` module: zero
  importers and unimportable since the resource-providers retirement (a prior
  removal in PR #172 was reverted wholesale by `b9a35ff1`, which restored the
  file; the CHANGELOG had kept claiming it was gone).
- Removed the dead `server/app/services/token_usage_capture.py` wrapper (its
  only caller, `pi_runner.py`, was deleted earlier; the lease-scoped
  replacements in `token_usage_lease.py` remain) and the orphaned
  `server/app/executors/agent_workspace.py`.
- Removed retired/unused config surface: the dead `PiRuntimeConfig` block
  and the unconsumed OpenClaw runtime knobs (`command_template`,
  `timeout_seconds`, `isolated_workspace_root`, `skill_safety`) — the admin
  instance-settings `openclaw` document is now `cwd`-only. Stored documents
  from older deployments are normalized at read time (retired keys stripped
  before response validation, no data migration needed), and
  `openclaw.skill_safety.repos[].ref` stays rejected at startup (config
  governance G3: refs are pinned by the DB `skill_lock` document only).
- Worker: removed the test-only `read_current_executions` compatibility
  helper and `strip_secret_config` (never called on the Worker — secret
  stripping happens Host-side in `split_manifest_config` before dispatch;
  verified no caller in repository history).
- Frontend: removed the orphaned video-hive player cluster
  (`VideoPlayer`, `InteractionOverlay`, `SubtitlePanel`, `NodePanel`,
  `videoNodeStore` and friends, ~1,030 LOC) plus `CollapsiblePanel`,
  `TimelineStrip`, `materialWeb.ts`, and the superseded
  `getFilterCounts`/`filterCountsCore` pair — all unreferenced since the
  react-query migration; pruned dead exports in `labels.ts`/`theme.ts`/
  `nodeCatalog.ts`/types, dead rules in `styles.css` (634 → 118 lines) and
  seven CSS modules; moved `@tanstack/react-query-devtools` to
  `dependencies` (it is imported by the production entry), moved
  `@types/dagre` to devDependencies, and dropped the redundant
  `@types/katex` shim (katex bundles its own types). The filter-count
  exclusion semantics (each dimension counts jobs matching the other
  filters while excluding its own) and the worker status-reader edge
  cases (dead writer, corrupt/missing file, started_at ordering) were
  re-homed onto the surviving `computeFilterCounts` / `read_runtime_status`
  implementations with ported tests.
- Removed one-off scripts whose retirement conditions are met:
  `backfill_workflow_revision_resources.py` (schema has moved v26 → v58 and
  the loader hard-rejects the `resources` field), `bench_gzip_exemption.py`,
  `velites_replay.py`, `velites_diff_events.py` (rollout archived),
  `backfill_failure_classification.py`, `backfill_worker_output_validation.py`,
  `migrate_job_dirs_to_shards.py` — each with its unit tests.
- Docs/deploy hygiene: `.env.example` and the READMEs no longer instruct the
  retired global worker-register-token setup (which now fails startup);
  `scripts/stack-prod-up.sh` drops the `agent_worker_register_token` prereq
  and the broken `funasr` warm-up block (the dependency left the image);
  references to the deleted `check-skills-shared.py` and the no-op
  `verify_specs.py` gate step are cleaned up.


- CSRF negative-path test: cookie-authenticated mutations without the
  `x-agent-legion-request` header are rejected with 403 (SECURITY-AUTH-001).

### Security

- Shared-database schema guard: `init_db` refuses to initialize/migrate the
  bare shared `agent_legion` database (the code-default DSN) unless
  `AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1` is set — prod launchers
  (native-prod-up.sh, deploy/compose.host.yaml) set it, while a misdirected
  process (worktree script without .env resolving the default DSN) fails
  with remediation instead of pushing unreleased migrations onto prod
  (2026-08-27: an export_openapi run applied v59-61 to the shared database
  this way). `scripts/export_openapi.py` additionally refuses to run at all
  against the shared database before the app is built.
- Skills runs dir (per-execution skill snapshots + cache locks) moved from
  `~/.agents/skills/agent-legion.runs` to a deterministic per-user OS temp
  dir (`agent-legion-skills.runs[-<uid>]`), overridable via
  `AGENT_LEGION_SKILLS_RUNS_DIR`: leaked snapshots no longer pollute the
  agent skills namespace, and the OS temp TTL backstops them. The temp root
  is created/validated with CPython tempfile trust rules (atomic `mkdir
  0700`; on reuse it must be a non-symlink directory owned by the current
  user, mode normalized to 0700) — closing pre-creation/symlink attacks on
  shared `/tmp`. The leak GC (see Changed) reuses the same validation, and
  the `.locks` dir is 0700 with symlink rejection (EXEC-SKILL-RUNS-SCRATCH-001).

### Changed

- Repacked the 19 underscore-prefixed private modules under
  `server/app/services/` into real subpackages (issue #199, completing the
  cluster-repack pattern proven by #191 and #234): `job_rerun/`
  (batch / by_failure_results / eligibility / preview / preview_checks /
  single / upstream_guard, plus the batch delete / run-to loops from
  `_job_batch_ops` as `batch_ops`), `ops_metrics/` (catchup / queue /
  queue_alert / runs / sampling / series / summary / workspace_sampling) and
  `failure_classification/` (markers / rules). Import sites were rewritten to
  the full new paths (no re-export facade). Each cluster's former flat entry
  module moved into its package: `job_rerun.py` and `failure_classification.py`
  became the package `__init__.py` (so `from server.app.services.job_rerun
  import JobRerunService` and the `failure_classification` attribute imports
  keep working unchanged, mirroring the #234 `status/` precedent), while
  `ops_metrics.py` became `ops_metrics/service.py` with `OpsMetricsService` /
  `Granularity` re-exported from the package root — a package shadows the
  same-named flat module, so keeping `ops_metrics.py` flat was not an option.
  Architecture baselines carry the old ceilings to the new path keys
  (file budgets via the #236 rename-floor rule; the service-data-boundary
  counts move as-is, with `job_rerun/__init__.py` newly registered at its
  observed bypass count).

- Repacked 21 of the flat `worker/` prefix-cluster modules into real
  subpackages (issue #234, mirroring #191 on the server side):
  `execution/` (heartbeat / lifecycle / prepare / run), `runtime/`
  (controls / models / preflight / setup), `upload/` (heartbeat / prepare /
  queue / scheduler), `host/` (client / status_sync / transfer),
  `artifact/` (download / upload), `registration/` (retry / token) and
  `status/` (the former `status.py` reporter as the package root, plus
  aggregates / reader). Import sites were rewritten to the full new paths
  (no re-export facade); `from worker.status import …` keeps working because
  the reporter now lives in `status/__init__.py`. Entry-point modules stay
  at the package root — `worker.service`, `worker.executor`, `worker.cli` —
  so the Dockerfile ENTRYPOINT, Makefile targets and
  `scripts/native-prod-up.sh` keep working; the `service` / `cli` clusters
  (`service_bind` / `service_models` / `cli_args`) stay flat because a
  `worker/<name>/` package would shadow the `worker/<name>.py` entry module
  and break `python -m worker.<name>` (Python resolves the package first).
  The workerctl standalone COPY is unchanged, and the worker image smoke
  import now covers `worker.upload.queue`.

- **Breaking (API consumers):** workspace id and workflow key are one
  identifier (schema v62, DB-WORKSPACE-KEY-BINDING-001): `POST
  /api/workspaces` now requires an explicit `id`
  (`^[a-z0-9][a-z0-9_-]{0,63}$`) that is bound to `default_workflow_key` at
  creation and immutable afterwards — `workflow_mode` and the
  `default_workflow_key` create/update fields are removed (422 on extra
  fields, 400 on any later key change), workspace creation no longer seeds
  the sample template (demo workspaces are provisioned by `make import-demo`
  / `scripts/seed_demo.py`), and the first-publish key adoption path is gone
  (mismatched draft keys are rejected with 422). The v62 migration renames
  existing workspaces to id == key (cascading `workspace_id` through every
  child table plus the FK-less `auth_scoped_tokens` and
  `ops_metric_samples`, fail-fast on id conflicts) and backfills
  never-published workspaces with key = id; `default_workflow_key` is
  deprecated as a separate concept pending full retirement (issue #211).
  Legacy workspace URLs change accordingly (e.g. `/workspaces/demo` →
  `/workspaces/education_video_problems_generation`).

- **Breaking (deployments):** the global worker register token is retired —
  registration uses workspace-scoped tokens only, issued per workspace in the
  admin UI (workspace 设置 → Agent 与 Worker, workspace is now mandatory at
  issuance) and managed in the Worker console's new "Workspace 访问" panel;
  leftover `AGENT_LEGION_WORKER_REGISTER_TOKEN(_FILE)` env vars or yaml
  `agent_workers.register_token(_file)` keys now fail startup with migration
  guidance (#35, schema v58).
- Worker registration presents all configured scoped tokens in one call
  (`X-Agent-Worker-Register-Tokens`); the Host resolves the union workspace
  scope, rejects the whole registration when any token is revoked, and returns
  per-workspace rows (id + name) so the console labels each token (#35).
- `GET /api/agent-workers?workspace_id=...` narrows to workers registered
  with that workspace's tokens; each workspace's settings page shows a
  read-only worker list, while legacy `[]`-scope (global-token) workers are
  admin-visible only until re-registered (#35).
- Compose stacks no longer mount `agent_worker_register_token`; workers get
  their scoped token via the console or `workerctl configure
  --register-token-file` (#35).
- **Breaking (deployment):** `server.app.main` no longer exports a
  module-level `app`; launchers must use the factory form
  (`uvicorn server.app.main:create_prod_app --factory`). Importing the
  module is now side-effect free — the `AGENT_LEGION_SKIP_MODULE_APP` env
  escape hatch is retired.
- Schema upgrades record one `schema_migrations` row per version and only
  run data migrations above `max(applied)`; legacy single-row installs are
  a no-op (DB-SCHEMA-001).
- Sandbox argv/env/read-roots construction and the registration protocol
  constants live once in `shared/` (imported by both Host and Worker),
  replacing the cross-side "keep in sync" copies; network opt-in is now
  strictly `is True` on the Worker path too (P-0.5 semantics).
- The workflow worker's mutable state moved from ~18 thread-private
  attributes (reached into by sibling modules) into an explicit
  `WorkflowWorkerState` container consumed as `worker.state.X`.
- Studio layout components consume `useWorkflowStudio()` through
  `StudioStateContext`/`StudioViewContext` instead of threading the whole
  ~35-field object as props through six layers; the fabricated
  `WorkflowDefinitionRecord` in job detail is replaced by a minimal
  `NodeCatalog` type.

- Skills runs dir leak GC: the sweeper thread now removes execution
  snapshot dirs older than 1h (mtime-based; `.locks`, non-directories and
  symlinks untouched) — a hard crash between snapshot copy and the
  finally-cleanup previously leaked the snapshot permanently. Deployments
  with per-process temp dirs (systemd `PrivateTmp`, or a host CLI sharing
  the skill cache with a containerized server) must pin
  `AGENT_LEGION_SKILLS_RUNS_DIR` to keep the FileLock domain whole.
### Added

- Service data-boundary ratchet (BOUNDARY-DATA-001): new services under
  `server/app/services/` must reach the database through the `JobQueries`
  facade; existing raw-SQL/DB-primitive counts are frozen in
  `config/architecture/service-data-boundary-baseline.json` and only
  ratchet down.

## [0.3.0-alpha] - 2026-08-25

### Added

- Workspace materials store: S3-compatible presigned direct upload (RustFS
  locally), content-addressed local material cache for sandboxed nodes,
  add-items dialog, demo workspace material seeding, and a storage readiness
  probe in `/api/health` plus a startup self-check (#141).
- Item-based run creation API `POST /workspaces/{id}/runs` with typed items
  (#141).
- Mandatory `type: start` entry node in every workflow DAG carrying the
  `accepted_item_types` entry contract; item types `material` and `ref`
  (#156, #161).
- `bundle` item type: a folder as a single item (`material_bundles`,
  manifest-referenced members, two-way delete guard, deterministic
  hardlink-tree materialization, bundle upload panel in the UI) (#156, #164).
- Job artifacts unified into instance object storage
  (`jobs/{workspace_id}/{job_id}/{name}` keys + `job_artifacts` manifest
  table, schema v54); the local job_dir is now an evictable cache (#160).
- Worker `max_code_concurrency` hot-reload via the console /
  `PUT /api/config` without restart (#123).
- `scripts/resume-workspaces.sh` (on-demand workspace scheduling resume) and
  `scripts/trim_terminal_code_manifests.py` (drain legacy code manifest
  rows).
- Optional bundled RustFS in prod-up (#150).

### Changed

- **Breaking (API consumers):** `job_batches` migrated to first-class `runs`
  (schema v53) (#141).
- Studio chat MCP loopback is served over an in-app streamable-HTTP endpoint
  (`/api/studio-agent/mcp`) with scoped, workspace-bound tokens and sliding
  TTL (#157, #158, #159).
- Worker artifact return goes through claim-injected presigned S3 staging;
  the local `/api/artifacts` CAS remains as the legacy fallback (missing
  upload specs, direct-upload failure, or crash recovery re-enters the old
  channel) (#160).

### Fixed

- `agent_execution_requests` TOAST bloat (#142): the queued kind='code'
  manifest persists only a lightweight `runtime_context` audit stub
  (job/workspace ids + `batch_id`/`batch_hash`); the full DB-derived payloads
  (job, workspace, intake batch, skill_versions) are rebuilt on the
  claim-response path in memory, never persisted. Terminal code rows are
  slimmed back to the stub automatically; `scripts/trim_terminal_code_manifests.py`
  drains legacy pre-fix rows (ops-side `VACUUM FULL`/`pg_repack` still needed
  to reclaim disk).
- Terminal-bundle reap moved off the startup-critical sweep (#139).
- init-worktree S3 bucket step silently skipped on bare `load_dotenv` (#163).
- Studio chat MCP loopback deadlock and message interleaving; fully async
  httpx (#157).
- Material delete guards and endpoint precedence (#151, #153).
- Materials & runs v1 follow-ups (#154, #155).
- Performance: trigger-maintained workspace job node status counts (schema
  v56, #121), forced index for expired node-run sweep page reads (#122),
  and per-pass claim-input memos in the dispatch path (#124).
- Artifact store durability (#168): rerun promotes now back up pre-existing
  authority objects and roll back on mid-batch copy failure; cache eviction
  re-validates the job state before every unlink; empty worker-reported
  `content_hash` registers the Host-computed digest; quality artifact
  contents read bounded streams instead of whole objects.

## [0.2.0] - 2026-08-20

### Changed

- **Breaking:** velites provider/model configuration now uses the runtime-owned
  `~/.velites/models.json` registry. Worker discovery fails closed when the
  registry, requested model, or referenced credential is unavailable.
- Worker model capabilities are runtime-scoped `(runtime, provider, model)`
  triples under protocol v3. Rolling upgrades must update the Host before
  Workers so an older Host cannot erase the runtime dimension.
- The Worker discovers models through each selected agent runtime and applies
  its local runtime-scoped allowlist instead of treating one static list as
  shared by all harnesses.

### Added

- Native OpenAI-compatible Chat Completions and Anthropic Messages provider
  drivers in velites, including tool use, streaming, usage accounting, and
  Anthropic extended-thinking continuation state.
- Secure Docker credential injection for environment references used by the
  velites model registry.

## [0.1.0] - 2026-08-19

Initial open-source release.

### Added

- Workspace-scoped DAG workflows: nodes declare business `capability` only;
  the authoritative definition is the workspace's active revision, published
  from Studio drafts.
- Batch job intake with workflow-defined intake modes.
- Pluggable agent runtimes: Pi CLI and velites (Rust harness with a
  pi-compatible event stream); per-agent `runtime` selection.
- Versioned external skills: `{repo, ref}` sources and pinned commit locks in
  the DB (`skill_sources` / `skill_lock`), managed via admin UI or
  `make skills-lock`.
- Local and remote execution: executor leases for local capacity; remote
  Agent Workers register over HTTP, claim executions (agent and code nodes),
  and upload artifacts.
- Real-time console: React SPA with live DAG view, SSE dashboard events,
  WebSocket agent status, run logs, artifacts, and token-usage statistics.
- Secrets vault: Fernet-encrypted workspace secrets and instance-level
  external service connections; configs carry `secret_ref` only.
- Multi-user access control: cookie sessions with CSRF guard, admin user
  management, per-workspace editor/viewer membership.
- PostgreSQL control plane (PostgreSQL 17) coordinating multi-process and
  multi-machine scheduling.
- Demo workflow `education_video_problems_generation` under `examples/`,
  runnable out of the box against a real LLM.
- Docker deployment stacks (`deploy/`) and remote worker deployment runbook.

[Unreleased]: https://github.com/LuciusCao/agent-legion/compare/v0.4.0-alpha...HEAD
[0.4.0-alpha]: https://github.com/LuciusCao/agent-legion/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/LuciusCao/agent-legion/compare/v0.2.0...v0.3.0-alpha
[0.2.0]: https://github.com/LuciusCao/agent-legion/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/LuciusCao/agent-legion/releases/tag/v0.1.0
