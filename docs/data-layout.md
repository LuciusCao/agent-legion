# data/ 目录布局

`data/` 是 Agent Legion 的运行时数据根目录（已 gitignore，禁止提交）。本文从代码推导**权威布局**：各子目录由哪个组件持有、内容是什么、生命周期如何。某个实例 `data/` 下实际存在的目录只是该实例的运行时残留，不作为布局依据——未在下文出现的子目录均为历史或本地残留，可安全删除。

数据根的位置由 `config/app.yaml` 的 `data_dir`（默认 `data`）决定，可被环境变量 `AGENT_LEGION_DATA_DIR` 覆盖（`server/app/settings.py:206-210`）。Host 启动时确保 `data/` 及其下 `videos/`、`logs/`、`packages/`、`jobs/` 存在（`server/app/settings.py:215-220`）。

## 1. Host 侧子目录

| 子目录 | 持有者 | 内容与结构 | 生命周期 |
|--------|--------|------------|----------|
| `jobs/` | Workflow / Executor 运行时 | Job 运行产物：`jobs/<workspace>/<job_id>/runs/<node_key>/<token>/`，token 目录下含 `session/` 与 Pi 事件流等执行产物（`server/app/storage_paths.py:240-283`） | 运行时产物。由后台 cleanup 按 `cleanup.run_dir_retention_days`（默认 3 天）清理过期 run dir；默认每个节点只保留最新一次 run（`server/app/services/log_cleanup.py:21-82`） |
| `logs/` | 节点执行日志 + workflow worker | 节点日志 `logs/jobs/<job_id>-<node_key>.log`（`server/app/storage_paths.py:249`）、`workflow_worker_pass.log`（`server/app/workflow_worker/pass_log.py:24`） | 日志。按 `cleanup.log_retention_days`（默认 7 天）清理；删除 Job 时日志先移入 `logs/jobs/.trash/<operation_id>/` 再清除（`server/app/services/job_deletion.py:109-172`） |
| `videos/` | 视频能力（video_knowledge workspace） | 视频文件 `videos/<video_id>/<video_id>.mp4`（`server/app/video_capabilities/_video_paths.py:25`） | 内容产物，随视频记录生命周期 |
| `packages/` | Workspace 打包导出 | 导出包 `packages/workspace-<workspace_id>/workspace-jobs-*.zip`（`server/app/pipeline/workspace_package.py:23-32`、`server/app/services/job_packages.py:94`） | 导出产物，可重新生成 |
| `artifacts/` | `ArtifactStore` | 内容寻址存储：`artifacts/<digest[:2]>/<digest>`，外加 `.staging/` 暂存区（`server/app/services/artifact_store.py:46-57`） | Worker 回传 artifact 的持久存储，带 GC grace |
| `agent_bundles/` | `AgentExecutionBroker` / dispatch | 派发给 Worker 的 bundle `<execution_id>.tar.gz`，Worker 回传的结果包 `*.result.tar.gz`（`server/app/agent_broker/dispatch.py:118`、`server/app/agent_result_commit.py:34-37`） | 在途传输文件。结果提交后即删除，孤儿文件由 reaper 清扫（`server/app/agent_broker/broker.py:299-306`、`server/app/agent_broker/reaper.py:55-60`） |

清理节奏由 `config/app.yaml:11-14` 的 `cleanup` 段控制（`log_retention_days`、`run_dir_retention_days`、`interval_seconds`），加载逻辑见 `server/app/services/log_cleanup.py:21-34`。

所有落盘路径都经过 `server/app/storage_paths.py` 的 `resolve_data_path` / `resolve_managed_path` 约束，保证存储路径不会逃出各自 managed root；受管理的顶层类别为 `videos`、`jobs`、`logs`、`packages`（`server/app/storage_paths.py:6`）。

## 2. Worker 侧目录

Worker 不读写 Host 的 `data/`，它持有自己的两个根：

| 目录 | 持有者 | 内容 | 生命周期 |
|------|--------|------|----------|
| work root | Worker 执行进程 | 每次执行一个 execution dir，内含解包的 bundle、执行产物与结果 | 可删除缓存/在途状态。配置项 `work_root`，默认 `/var/lib/agent-legion-worker`（`worker/executor.py:234`、`config/agent-worker.example.yaml:29`）。supervisor 启动时 `clean_work_root` 清掉崩溃残留目录，但带 `upload_pending.json` 标记的目录保留到结果上报完成（`worker/cleanup.py:16-25`、`worker/upload_queue.py:38`） |
| 状态目录 | Worker Service（控制面） | 导入后的可写 `worker.yaml`、`control_token`（0600）、`register_token`、运行状态与指标缓存（`worker/config_store.py:118-167`） | 持久配置状态，不可随意删除。容器内为 `--state-dir /var/lib/agent-legion-worker-control`（`Dockerfile:72`）；本地运行默认 `data/agent-worker-service`（`worker/cli_args.py:34`、`worker/service.py:149`），即落在仓库 `data/` 下 |

`upload_pending.json` 是 UploadQueue 的持久化标记：任务入队前写入 execution dir，Host 接受结果后才删除；Worker 重启时按标记恢复未上报的结果（`worker/upload_queue.py:1-17`）。

## 3. 部署形态映射

- `deploy/compose.host.yaml`：Host 服务设 `AGENT_LEGION_DATA_DIR=/var/lib/agent-legion` 并挂载命名卷 `host-data`（`:30-36`）；同机 Worker 挂 `worker-data` → `/var/lib/agent-legion-worker`、`worker-control` → `/var/lib/agent-legion-worker-control`（`:57-61`）；PostgreSQL 数据在独立卷 `postgres-data`（`:12-13`）。
- `deploy/compose.worker.yaml`：独立部署的 Worker 只挂 `worker-data` 与 `worker-control` 两个卷（`:14-15`）。

## 4. 多 worktree 隔离

- 每个 worktree 使用独立的 `data/` 目录与独立端口，互不覆盖运行时状态（`AGENTS.md` 第 1 节；`docs/architecture/project-structure.md:107`）。
- `data/` 下**全部**内容都是每 worktree 独立的：运行时状态、产物、日志、缓存都不跨 worktree 共享。需要非默认位置时用 `AGENT_LEGION_DATA_DIR` 显式指定。
- 本地运行 Worker Service 时，其状态目录默认在 `data/agent-worker-service/`，同样随 worktree 隔离。

## 参考

- `server/app/settings.py:195-245` — data 根解析与受管子目录创建
- `server/app/storage_paths.py` — managed root 路径约束与 `jobs/`、`logs/` 结构
- `server/app/services/log_cleanup.py`、`config/app.yaml:11-14` — 日志与 run dir 保留策略
- `server/app/services/artifact_store.py`、`server/app/agent_broker/broker.py` — `artifacts/` 与 `agent_bundles/`
- `worker/executor.py:228`、`worker/cleanup.py`、`worker/upload_queue.py`、`worker/config_store.py` — Worker work root 与状态目录
- `deploy/compose.host.yaml`、`deploy/compose.worker.yaml` — 容器卷映射
