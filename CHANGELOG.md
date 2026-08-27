# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/) once 1.0.0 is released.

## [Unreleased]

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

### Security

- CSRF negative-path test: cookie-authenticated mutations without the
  `x-agent-legion-request` header are rejected with 403 (SECURITY-AUTH-001).

### Changed

- **Breaking (deployments):** the global worker register token is retired —
  registration uses workspace-scoped tokens only, issued per workspace in the
  admin UI (设置 → Worker Token, workspace is now mandatory at issuance) and
  managed in the Worker console's new "Workspace 访问" panel; leftover
  `AGENT_LEGION_WORKER_REGISTER_TOKEN(_FILE)` env vars or yaml
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

[Unreleased]: https://github.com/LuciusCao/agent-legion/compare/v0.3.0-alpha...HEAD
[0.3.0-alpha]: https://github.com/LuciusCao/agent-legion/compare/v0.2.0...v0.3.0-alpha
[0.2.0]: https://github.com/LuciusCao/agent-legion/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/LuciusCao/agent-legion/releases/tag/v0.1.0
