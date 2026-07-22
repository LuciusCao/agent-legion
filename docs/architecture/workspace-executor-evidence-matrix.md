# Phase 1-5 Workspace Executor Evidence Matrix

This matrix records the reverse audit of Phase 1-5 Workspace Executor architecture promises.

- `Verified` — the promise already meets the evidence rule and is registered as an invariant.
- `Gap` — the promise is approved but evidence is missing; linked to the task that will close it.
- `Deferred` — the promise is out of scope for Phase 1-5 migration evaluation.
- `Behavior-only` — the promise is tested but not elevated to a durable invariant.

## Matrix

| Promise | Boundary | Invariant ID | Quick evidence | Full evidence | Result | Follow-up task |
| --- | --- | --- | --- | --- | --- | --- |
| Generated frontend API types remain synchronized with the backend OpenAPI schema | contract | API-CONTRACT-001 | `scripts/generate-api-types.sh` | N/A | Verified | N/A |
| Routes, services, repositories, and executor adapters preserve dependency direction | layer | BOUNDARY-LAYER-001 | `scripts/check_architecture.py` | N/A | Verified | N/A |
| Video and job filesystem operations stay inside managed roots and reject symlink escapes | filesystem | SECURITY-PATH-001 | `tests/test_storage_paths.py` | `tests/full/test_storage_path_corruption.py` | Verified | N/A |
| Job deletion is conflict-safe and has one guarded mutation boundary | database/service | DB-MUTATION-001 | `tests/test_job_deletion_service.py` | `tests/full/test_job_deletion_races.py` | Verified | N/A |
| Continue/resume accepts only resumable paused states and never reopens terminal jobs | service/API | EXEC-STATE-001 | `tests/test_workspace_job_control_flow.py` | N/A | Verified | N/A |
| Video detail GET is read-only; cache persistence occurs only on explicit write/event paths | route/service | BOUNDARY-READ-001 | `tests/test_video_read_service.py` | N/A | Verified | N/A |
| video_capabilities modules must not import orchestration or runtime modules | video_capabilities | BOUNDARY-VIDEO-001 | `scripts/check_architecture.py`, `tests/test_architecture_video_legacy.py` | N/A | Verified | N/A |
| Production app and router code must not reintroduce legacy Video Hive route modules after migration | route | BOUNDARY-VIDEO-002 | `scripts/check_architecture.py`, `tests/test_architecture_video_legacy.py` | N/A | Verified | N/A |
| New Workspace code must not import legacy pipeline phase executor modules | workspace/executor | BOUNDARY-VIDEO-003 | `scripts/check_architecture.py`, `tests/test_architecture_phase6.py`, `tests/test_architecture_video_legacy.py` | N/A | Verified | N/A |
| Workspace runtime stats derive from executor allocations, bindings, leases, and capacities | executor/service | EXEC-STATS-001 | `tests/test_workspace_configuration_service.py::test_executor_stats_report_configured_capacity_and_leases`, `tests/test_workspace_configuration_service.py::test_executor_stats_does_not_consult_agent_status_manager`, `tests/routes/jobs/test_workspace_stats.py::test_workspace_stats_executor_status_reflects_allocations_and_leases` | N/A | Verified | N/A |
| PostgreSQL connections are pooled and deterministically returned | database | DB-LIFECYCLE-001 | `tests/db/test_postgres_runtime.py` | N/A | Verified | N/A |
| Runtime queues and job event sequence numbers are persisted in PostgreSQL; no new in-process authoritative runtime state | database | DB-STATE-001 | `tests/test_agent_broker.py`, `tests/test_job_event_buffer_db.py`, `tests/ci/test_postgres_only_runtime.py` | N/A | Verified | N/A |
| Executor global capacity is shared across workspaces and never exceeded | executor | EXEC-CAPACITY-001 | `tests/test_workflow_worker_concurrency.py::test_global_capacity_enforced_by_lease_transaction`, `tests/workers/test_workflow_worker_capacity.py` | `tests/full/test_executor_worker_fairness.py::test_shared_capacity_and_bounded_fairness` | Verified | N/A |
| Runnable workspaces receive bounded scheduling opportunity under contention | scheduler/worker | EXEC-FAIRNESS-001 | `tests/test_workflow_worker_concurrency.py::test_round_robin_allows_small_workspace_to_claim` | `tests/full/test_executor_worker_fairness.py::test_shared_capacity_and_bounded_fairness` | Verified | N/A |
| Lease loss and shutdown produce bounded adapter-specific cancellation without leaking executor capacity | executor/worker | EXEC-CANCEL-001 | `tests/test_executor_cancellation.py` | `tests/full/test_executor_cancellation_recovery.py` | Verified | N/A |
| PostgreSQL schema initialization is transactional, advisory-locked, and idempotent | database/migration | RECOVERY-MIGRATION-001 | `tests/db/test_postgres_runtime.py::test_schema_initialization_is_idempotent` | `tests/full/test_agent_worker_control_plane.py::test_postgres_agent_schema_initialization_is_idempotent` | Verified | N/A |
| Offline SQLite import preserves rows and IDs and refuses populated targets | database/import | RECOVERY-BACKUP-001 | `tests/db/test_sqlite_import.py` | N/A | Verified | N/A |
| Secrets use environment overrides and enabled runtime dependencies fail validation at startup | config | CONFIG-STARTUP-001 | `tests/test_settings.py`, `tests/test_startup_validation.py` | N/A | Verified | N/A |
| Workspace job lifecycle SSE events are broadcast only after the underlying transaction commits | events/service | EXEC-EVENT-001 | `tests/test_job_events.py` | `tests/full/test_workspace_sse.py` | Verified | N/A |
| Source file line budgets are enforced and temporary overruns are tracked | architecture | CONFIG-ARCH-001 | `scripts/check_architecture.py`, `tests/test_check_architecture.py` | N/A | Verified | N/A |
| Agent Nodes enter running only after a compatible Worker atomically claims capacity in both the Workflow Node and Worker domains | agent/worker | EXEC-AGENTCLAIM-001 | `tests/test_agent_broker.py`, `tests/routes/test_agent_workers.py` | `tests/full/test_agent_worker_control_plane.py::test_agent_capacity_matrix_across_workers` | Verified | N/A |
| Agent definitions own capability, runtime, skill, tools, and requirements while Workers declare only runtime compatibility and machine capacity | agent/catalog | EXEC-AGENTDEF-001 | `tests/test_agent_catalog.py`, `tests/test_agent_broker.py::test_worker_registration_declares_runtime_and_machine_capacity` | `tests/full/test_agent_worker_control_plane.py::test_agent_definition_catalog_snapshot_lifecycle` | Verified | N/A |
| Executor kinds resolve through the kinds registry; isinstance dispatch chains over executor config types are forbidden in registry.py | executor/config | EXEC-KIND-001 | `tests/executors/test_executor_kind_registration.py::test_unknown_kind_rejected_at_config_load`, `tests/test_architecture_execution_decoupling.py` | N/A | Verified | N/A |
| Agent Worker credentials are per-worker and revocable; only sha256 hashes of token secrets are persisted | security/worker | SECURITY-WORKER-001 | `tests/routes/test_agent_workers.py`, `tests/test_agent_broker.py::test_worker_registration_declares_runtime_and_machine_capacity` | `tests/full/test_agent_worker_control_plane.py::test_worker_token_is_hashed_and_revocable` | Verified | N/A |
| Shard executions are claimed through ExecutorLeaseRepository.try_claim with one lease per shard; fan-out never bypasses the capacity system | executor/scheduler | EXEC-SHARD-001 | `tests/workflows/test_sharding.py` | `tests/full/test_shard_fanout_e2e.py` | Verified | N/A |
| Executor adapters are substitutable through typed runtime configuration | executor/config | Behavior-only | `scripts/check_architecture.py`, `tests/test_executor_runtime.py` | N/A | Behavior-only | N/A |
| Worker lease/restart/shutdown preserves capacity accounting | executor/worker | Behavior-only | `tests/test_executor_recovery.py`, `tests/workers/test_workflow_worker_thread_local.py` | N/A | Behavior-only | N/A |
| Allocation/binding/local-limit updates are atomic | workspace/config | Behavior-only | `tests/test_workspace_configuration_service.py` | N/A | Behavior-only | N/A |
| Legacy workspace agent/pipeline paths are retired | legacy | Behavior-only | `tests/executors/legacy/test_dry_run.py`, `tests/executors/legacy/test_interruption.py`, `tests/executors/legacy/test_materialize.py`, `tests/executors/legacy/test_startup.py` | N/A | Behavior-only | N/A |
| Phase 6 workspace UI behavior remains decoupled from backend internals | frontend | Deferred | N/A | N/A | Deferred | `docs/superpowers/plans/2026-06-13-workspace-capability-experience-parity-phase-6.md` |
