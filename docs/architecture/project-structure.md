# 项目结构

本文件列出 Agent Legion 仓库的完整目录结构。更精简的模块说明见 [backend.md](backend.md)、[frontend.md](frontend.md) 和 [pipeline.md](pipeline.md)。

```text
video-hive/
├── pyproject.toml              # Python project metadata, dependencies, tool config
├── uv.lock                     # Locked Python dependency tree
├── config/
│   ├── app.yaml                # Application paths, HTTP settings, worker concurrency
│   ├── video_hive.yaml         # ASR, CMS, resource providers, cleanup, openclaw
│   └── workflow.yaml           # Workspace executors and workflow runtime settings
├── server/
│   ├── app/
│   │   ├── main.py             # FastAPI app factory + lifespan worker threads
│   │   ├── routes/             # REST API routes (videos, agents, worker, artifacts, packages, jobs, workspaces, contracts)
│   │   │   ├── agents.py                  # Agent status tracking
│   │   │   ├── artifacts.py               # Video artifact access
│   │   │   ├── common.py                  # Shared route helpers
│   │   │   ├── executor_contracts.py      # Executor contract schemas
│   │   │   ├── job_artifacts.py           # Job artifact access
│   │   │   ├── job_batches.py             # Workspace job batch creation
│   │   │   ├── job_contracts.py           # Job request/response contracts
│   │   │   ├── job_http.py                # Job HTTP helpers
│   │   │   ├── job_operation_contracts.py # Job operation contracts
│   │   │   ├── job_view_contracts.py      # Job view/read contracts
│   │   │   ├── jobs.py                    # Generic workspace job routes
│   │   │   ├── packages.py                # Video packaging routes
│   │   │   ├── questions.py               # Question annotation routes
│   │   │   ├── video_hive.py              # Legacy video-hive API surface
│   │   │   ├── videos.py                  # Video queue routes
│   │   │   ├── worker.py                  # Worker control routes
│   │   │   ├── workflow_catalog.py        # Workflow catalog routes
│   │   │   ├── workflow_contracts.py      # Workflow contract schemas
│   │   │   ├── workspace_configuration.py # Executor allocations, bindings, and local node limits
│   │   │   ├── workspace_executors.py     # Executor registry and workspace allocations
│   │   │   ├── workspace_runs.py          # Node run lifecycle and rerun
│   │   │   ├── workspace_settings.py      # Workspace resource/intake settings
│   │   │   └── workspaces.py              # Workspace CRUD
│   │   ├── db/                 # SQLite database wrapper (schema, queries, notifications)
│   │   │   └── migrations/     # Versioned schema migrations (v001–v009, registry, runner, report)
│   │   ├── executors/          # Phase 5 executor runtime (registry, runtime, config, pi, openclaw, local, leases, scheduling, legacy_migration, path canonicalization)
│   │   │   ├── runtime_config.py # Executor runtime configuration loader
│   │   ├── cms/                # CMS API integration (auth, client, knowledge, question)
│   │   ├── jobs/               # Job queries for Agent Legion workflow
│   │   ├── services/           # Business logic services
│   │   │   ├── intake.py       # Video intake (add, URL resolution)
│   │   │   ├── video_actions.py # Batch rerun, delete, package selection
│   │   │   ├── manual_run.py   # Manual phase run orchestration
│   │   │   └── interaction_stats.py # Interaction statistics aggregation
│   │   ├── settings.py         # Settings loader from YAML
│   │   ├── worker.py           # Background worker loop + per-video phase processing
│   │   ├── worker_control.py   # Worker pause/resume control
│   │   ├── worker_thread.py    # Background worker thread lifecycle
│   │   ├── worker_scheduler.py # Worker scheduling logic
│   │   ├── workflow_worker_thread.py # Agent Legion workflow worker thread
│   │   ├── events.py           # SSE event broadcaster
│   │   ├── agents.py           # OpenClaw agent discovery and status tracking
│   │   ├── records.py          # TypedDict type definitions for DB records
│   │   ├── pipeline/           # Video pipeline stage implementations
│   │   │   ├── common.py       # URL-to-id parsing, SRT parse/format helpers
│   │   │   ├── phases.py       # Phase list and agent-phase definitions
│   │   │   ├── download.py     # HTTP video downloader
│   │   │   ├── transcribe.py   # ASR providers (whisper.cpp / SenseVoice) + fallback logic
│   │   │   ├── transcribe_sensevoice.py # SenseVoice-specific runner
│   │   │   ├── openclaw.py     # OpenClaw command runner
│   │   │   ├── openclaw_sessions.py # OpenClaw runner session management
│   │   │   ├── assemble.py     # Final metadata.json assembly
│   │   │   ├── artifacts.py    # Artifact cleanup on rerun
│   │   │   ├── reader.py       # Artifact reader for the API
│   │   │   ├── package.py      # ZIP packaging of completed videos
│   │   │   ├── upload_params.py # Assemble upload_params.json in llm_claude format
│   │   │   ├── runners.py      # OpenClaw runner pool
│   │   │   ├── recovery.py     # Interrupted video recovery on startup
│   │   │   ├── validators.py   # Input validators
│   │   │   └── references/     # Markdown prompt references for openclaw phases
│   │   │       ├── phase-03-subtitle-review.md
│   │   │       ├── phase-04-chapter-generate.md
│   │   │       ├── phase-05-interaction-generate.md
│   │   │       └── phase-06-content-review.md
│   │   ├── workflows/          # Agent Legion DAG workflow definitions
│   │   │   ├── definition.py   # Workflow definition loader
│   │   │   ├── execution_control.py # Pause/resume/continue job execution
│   │   │   ├── executor.py     # Workflow node executor
│   │   │   ├── scheduler.py    # DAG scheduling and downstream node resolution
│   │   │   ├── registry.py     # Workflow definition registry by key
│   │   │   ├── question_content.py # Question content workflow presets
│   │   │   ├── question_comprehension_info.py # Question comprehension workflow
│   │   │   ├── pi_runner.py    # Pi CLI runner for agent nodes
│   │   │   ├── artifacts.py    # Artifact validation and rerun cleanup
│   │   │   ├── skills.py       # Legacy skill path resolver / contract checker
│   │   │   ├── resources.py    # Resource provider bindings
│   │   │   └── skills/         # README explaining migrated Pi skills
│   │   └── skills/             # External Pi skill repo manager
│   │       ├── config.py       # Skill source registry loader
│   │       ├── errors.py       # Skill resolution errors
│   │       ├── lock.py         # Resolved commit lockfile handling
│   │       └── manager.py      # SkillManager: checkout / cache / path safety
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx            # React entry point
│       ├── App.tsx             # Router shell (React Router)
│       ├── api.ts              # Thin fetch wrapper
│       ├── generated/          # Auto-generated OpenAPI transport types
│       │   └── api.ts
│       ├── types.ts            # Shared TypeScript types
│       ├── labels.ts           # UI labels & phase lists
│       ├── helpers.ts          # Pure utility functions
│       ├── theme.ts            # MD3 CSS custom properties
│       ├── styles.css          # Global styles
│       ├── pages/              # Route-level pages
│       │   ├── DashboardPage.tsx
│       │   ├── DetailPage.tsx
│       │   ├── JobDetailPage.tsx
│       │   ├── ListPage.tsx
│       │   ├── SettingsPage.tsx
│       │   ├── VideoHiveSettingsPage.tsx
│       │   └── WorkspaceMainPage.tsx
│       ├── components/         # Reusable UI components
│       │   ├── AddDialog.tsx
│       │   ├── AgentStatusIndicator.tsx
│       │   ├── AppBar.tsx
│       │   ├── ArtifactDrawer.tsx
│       │   ├── ArtifactListDialog.tsx
│       │   ├── ArtifactPreviewDialog.tsx
│       │   ├── BatchDeleteDialog.tsx
│       │   ├── BatchRerunDialog.tsx
│       │   ├── BatchToolbar.tsx
│       │   ├── ChapterPanel.tsx
│       │   ├── ChapterStrip.tsx
│       │   ├── CreateWorkspaceDialog.tsx
│       │   ├── DagFullscreenDialog.tsx
│       │   ├── DagGraph.tsx
│       │   ├── DagNode.tsx
│       │   ├── DagStepper.tsx
│       │   ├── DeleteDialog.tsx
│       │   ├── DeleteWorkspaceDialog.tsx
│       │   ├── EmptyStateGuide.tsx
│       │   ├── ExecutorAllocationSection.tsx
│       │   ├── ExecutorBindingSection.tsx
│       │   ├── ExpandedJobPanel.tsx
│       │   ├── FilterChips.tsx
│       │   ├── InteractionOverlay.tsx
│       │   ├── InteractionReviewBadge.tsx
│       │   ├── JobActionBar.tsx
│       │   ├── JobDeleteDialog.tsx
│       │   ├── JobDetailActions.tsx
│       │   ├── JobList.tsx
│       │   ├── JobListItem.tsx
│       │   ├── JobLogDialog.tsx
│       │   ├── JobNodeStepper.tsx
│       │   ├── JobProgressPanel.tsx
│       │   ├── JobRerunDialog.tsx
│       │   ├── JobRunToDialog.tsx
│       │   ├── LaTeXSpan.tsx
│       │   ├── LaTeXText.tsx
│       │   ├── LocalNodeLimitSection.tsx
│       │   ├── MaterialIcon.tsx
│       │   ├── MetadataPanel.tsx
│       │   ├── MiniDag.tsx
│       │   ├── NodeDetailsPanel.tsx
│       │   ├── NodePanel.tsx
│       │   ├── NodeRunsTable.tsx
│       │   ├── PackageHistoryDialog.tsx
│       │   ├── PhaseRunsPanel.tsx
│       │   ├── PhaseStepper.tsx
│       │   ├── QuestionAnnotations.tsx
│       │   ├── QuestionContentPanel.tsx
│       │   ├── RerunDialog.tsx
│       │   ├── RunToDialog.tsx
│       │   ├── StatCards.tsx
│       │   ├── SubtitlePanel.tsx
│       │   ├── TimelineStrip.tsx
│       │   ├── Toast.tsx
│       │   ├── TranscriptionDetails.tsx
│       │   ├── VideoList.tsx
│       │   ├── VideoPlayer.tsx
│       │   ├── WorkspaceCard.tsx
│       │   └── WorkspaceStatCards.tsx
│       ├── hooks/              # React custom hooks
│       │   ├── useDebouncedCallback.ts
│       │   ├── useDetailPage.ts
│       │   ├── useJobComprehensionInfo.ts
│       │   ├── useJobQuestion.ts
│       │   ├── usePhaseRunsTimeline.ts
│       │   ├── useVideoEvents.ts
│       │   ├── useVideoPhaseEvents.ts
│       │   └── useWorkspaceEvents.ts
│       ├── layouts/            # Page layouts
│       │   ├── AppShell.tsx
│       │   ├── VideoHiveLayout.tsx
│       │   └── WorkspaceLayout.tsx
│       ├── lib/                # Domain helpers and pure utilities
│       │   ├── download.ts
│       │   ├── formatters.ts
│       │   ├── jobDag.ts
│       │   ├── jobRuns.ts
│       │   ├── latex.ts
│       │   ├── materialWeb.ts
│       │   ├── parsers.ts
│       │   ├── phases.ts
│       │   ├── questionHighlight.ts
│       │   ├── search.ts
│       │   └── workflowNodes.ts
│       └── stores/             # Zustand state stores
│           ├── artifactStore.ts
│           ├── detailStore.ts
│           ├── interactionStore.ts
│           ├── jobStore.ts
│           ├── packageStore.ts
│           ├── settingStore.ts
│           ├── uiStore.ts
│           ├── videoStore.ts
│           └── workspaceStore.ts
├── scripts/                    # Quality gates, migration finalizers, and generators
│   ├── check_architecture.py            # Architecture contract checker
│   ├── check_invariants.py              # Invariant/exemption registry validator
│   ├── check-ci.sh                      # CI quality gate
│   ├── check-fast.sh                    # Fast smoke gate
│   ├── check-pi.sh                      # Pi CLI smoke check
│   ├── check-quick.sh                   # Daily development gate
│   ├── check.sh                         # Full pre-commit gate
│   ├── export_openapi.py                # Export OpenAPI schema
│   ├── finalize-workspace-executor-migration.py # Phase 5 migration finalizer
│   ├── generate-api-types.sh            # Generate frontend API types
│   ├── generate_architecture.py         # Regenerate architecture doc tables
│   ├── install-git-hooks.sh             # Optional pre-commit hooks
│   ├── migrate-paths-to-relative.py     # One-time relative path migration
│   ├── migrate-skills-to-external-repos.py # Skill repo migration helper
│   ├── verify_specs.py                  # Spec health check
│   └── ...
├── tests/                      # pytest suite
│   ├── conftest.py             # Shared fixtures
│   ├── test_agents.py          # AgentStatusManager unit tests
│   ├── test_db.py              # Database tests
│   ├── test_events.py          # SSE event tests
│   ├── test_fetch_url.py       # CMS token/video lookup tests
│   ├── test_interaction_stats.py
│   ├── test_jobs.py            # Job model tests
│   ├── test_main.py            # App factory / lifespan tests
│   ├── test_openclaw_sessions.py
│   ├── test_security.py        # Security tests
│   ├── test_services.py        # Service layer unit tests
│   ├── test_settings.py
│   ├── test_skill_manager.py   # External skill manager tests
│   ├── test_storage_paths.py   # Relative path storage tests
│   ├── test_video_actions.py
│   ├── test_worker.py          # Worker-phase integration tests
│   ├── test_worker_scheduler.py
│   ├── test_worker_thread.py
│   ├── test_architecture_*.py  # Architecture contract tests
│   ├── test_executor_*.py      # Phase 5 executor tests
│   ├── test_job_*_service.py   # Workspace job service tests
│   ├── test_pipeline_*.py      # Pipeline stage unit tests
│   ├── test_relative_path_*.py # Relative path portability tests
│   ├── test_workspace_*.py     # Workspace/executor API tests
│   ├── test_workflow_*.py      # DAG workflow tests
│   ├── routes/                 # Route-level contract tests
│   ├── full/                   # Higher-fidelity full-gate tests
│   └── ci/                     # CI extended stress tests
└── data/                       # Runtime data (gitignored)
    ├── video_hive.sqlite
    ├── videos/
    ├── logs/
    ├── packages/
    └── jobs/
```
