# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/) once 1.0.0 is released.

## [Unreleased]

### Fixed

- `agent_execution_requests` TOAST bloat (#142): the queued kind='code'
  manifest persists only a lightweight `runtime_context` audit stub
  (job/workspace ids + `batch_id`/`batch_hash`); the full DB-derived payloads
  (job, workspace, intake batch, skill_versions) are rebuilt on the
  claim-response path in memory, never persisted. Terminal code rows are
  slimmed back to the stub automatically; `scripts/trim_terminal_code_manifests.py`
  drains legacy pre-fix rows (ops-side `VACUUM FULL`/`pg_repack` still needed
  to reclaim disk).

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

[Unreleased]: https://github.com/LuciusCao/agent-legion/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/LuciusCao/agent-legion/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/LuciusCao/agent-legion/releases/tag/v0.1.0
