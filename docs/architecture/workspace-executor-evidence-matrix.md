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
| Video and job filesystem operations stay inside managed roots and reject symlink escapes | filesystem | SECURITY-PATH-001 | `tests/test_storage_paths.py` | `tests/full/test_storage_path_corruption.py` | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-4` |
| Job deletion is conflict-safe and has one guarded mutation boundary | database/service | DB-MUTATION-001 | `tests/test_job_deletion_service.py` | `tests/full/test_job_deletion_races.py` | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-5` |
| Continue/resume accepts only resumable paused states and never reopens terminal jobs | service/API | EXEC-STATE-001 | `tests/test_workspace_job_control_flow.py` | N/A | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-5` |
| Video detail GET is read-only; cache persistence occurs only on explicit write/event paths | route/service | BOUNDARY-READ-001 | `tests/test_api.py` | N/A | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-6` |
| Workspace runtime stats derive from executor allocations, bindings, leases, and capacities | executor/service | EXEC-STATS-001 | `tests/test_workspace_configuration_service.py` | N/A | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-6` |
| SQLite connections opened by application and tests are deterministically closed | database | DB-LIFECYCLE-001 | `tests/test_sqlite_connection_lifecycle.py` | N/A | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-7` |
| Executor global capacity is shared across workspaces and never exceeded | executor | EXEC-CAPACITY-001 | `tests/test_pipeline_worker_concurrency.py` | `tests/full/test_executor_worker_fairness.py` | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-8` |
| Runnable workspaces receive bounded scheduling opportunity under contention | scheduler/worker | EXEC-FAIRNESS-001 | `tests/test_pipeline_worker_concurrency.py` | `tests/full/test_executor_worker_fairness.py` | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-8` |
| Lease loss and shutdown produce bounded adapter-specific cancellation without leaking executor capacity | executor/worker | EXEC-CANCEL-001 | `tests/test_executor_cancellation.py` | `tests/full/test_executor_cancellation_recovery.py` | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-9` |
| Interrupted migration is atomic or reopens into a recoverable pre-migration state | database/migration | RECOVERY-MIGRATION-001 | `tests/test_migration_runner.py` | `tests/full/test_migration_interruption_recovery.py` | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-10` |
| A created SQLite backup can be restored and reopened with invariant-preserving data | database/backup | RECOVERY-BACKUP-001 | `tests/test_executor_backup_restore.py` | `tests/full/test_backup_restore_drill.py` | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-10` |
| Secrets use environment overrides and enabled runtime dependencies fail validation at startup | config | CONFIG-STARTUP-001 | `tests/test_settings.py`, `tests/test_startup_validation.py` | N/A | Gap | `docs/superpowers/plans/2026-06-13-evolutionary-quality-gates-and-architecture-hardening.md#task-11` |
| Executor adapters are substitutable through typed runtime configuration | executor/config | Behavior-only | `scripts/check_architecture.py`, `tests/test_executor_runtime.py` | N/A | Behavior-only | N/A |
| Worker lease/restart/shutdown preserves capacity accounting | executor/worker | Behavior-only | `tests/test_executor_recovery.py`, `tests/test_pipeline_worker_thread.py` | N/A | Behavior-only | N/A |
| Allocation/binding/local-limit updates are atomic | workspace/config | Behavior-only | `tests/test_workspace_configuration_service.py` | N/A | Behavior-only | N/A |
| Legacy workspace agent/pipeline paths are retired | legacy | Behavior-only | `tests/test_executor_legacy_finalization.py` | N/A | Behavior-only | N/A |
| Phase 6 workspace UI behavior remains decoupled from backend internals | frontend | Deferred | N/A | N/A | Deferred | `docs/superpowers/plans/2026-06-13-workspace-capability-experience-parity-phase-6.md` |
