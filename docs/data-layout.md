# data/ 目录布局

`data/` 是 Agent Legion 的运行时数据根目录（已 gitignore，禁止提交）。本文从代码推导**权威布局**：各子目录由哪个组件持有、内容是什么、生命周期如何。某个实例 `data/` 下实际存在的目录只是该实例的运行时残留，不作为布局依据——未在下文出现的子目录均为历史或本地残留，可安全删除。

数据根的位置默认是仓库下 `data/`，由环境变量 `AGENT_LEGION_DATA_DIR` 覆盖（`config/app.yaml` 的 `data_dir` 键已退役；`server/app/settings.py`）。Host 启动时确保 `data/` 及其下 `videos/`、`logs/`、`packages/`、`jobs/` 存在（`server/app/settings.py`）。

## 1. Host 侧子目录

| 子目录 | 持有者 | 内容与结构 | 生命周期 |
|--------|--------|------------|----------|
| `jobs/` | Workflow / Executor 运行时 | Job 运行产物：`jobs/<workspace>/<shard>/<job_id>/runs/<node_key>/<token>/`，其中 `<shard>` 为 `sha1(job_id)` 前 2 位 hex（`server/app/jobs/storage_layout.py`），token 目录下含 `session/` 与 Pi 事件流等执行产物（`server/app/storage_paths.py` 的 `derive_session_dir_from_run_dir`）。旧扁平布局 `jobs/<workspace>/<job_id>/` 只读兼容：读取一律经 `jobs.storage_dir` 列解析，不搬迁不回填 | 产物权威副本在实例对象存储：`jobs/{workspace_id}/{job_id}/{name}` key + `job_artifacts` 清单表（`server/app/services/job_artifact_objects.py`）。本地 job_dir 只是执行暂存与可淘汰缓存：保留期清理走 `cleanup_old_logs`（`server/app/services/log_cleanup.py:51`），按 `cleanup.run_dir_retention_days`（默认 3 天）清理过期 run dir、每个节点只保留最新一次 run；另有容量淘汰（`AGENT_LEGION_JOB_CACHE_MAX_BYTES`，默认 50GiB，`server/app/services/job_artifact_maintenance.py`），只删清单已确认的文件。读路径本地命中直读、缺失回退对象存储（EXEC-ARTIFACT-STORE-001） |
| `logs/` | 节点执行日志 + workflow worker | 节点日志 `logs/jobs/<job_id>-<node_key>.log`（`server/app/services/job_run_dir_probe.py` 的 `derive_run_dir_from_log_path`）、`workflow_worker_pass.log`（`server/app/workflow_worker/pass_log.py`） | 日志。按 `cleanup.log_retention_days`（默认 7 天）清理；删除 Job 时日志先移入 `logs/jobs/.trash/<operation_id>/` 再清除（`server/app/services/job_deletion.py` 的 `delete_job`） |
| `videos/` | 视频内容产物（业务节点自建自用） | 平台仅在启动时创建目录；业务剥离后平台代码不再读写该目录 | 内容产物 |
| `packages/` | Workspace 打包导出 | 导出包 `packages/workspace-<workspace_id>/workspace-jobs-*.zip`（`server/app/services/workspace_package_create.py`、`server/app/services/job_packages.py`） | 导出产物，可重新生成 |
| `artifacts/` | `ArtifactStore` | 内容寻址存储：`artifacts/<digest[:2]>/<digest>`，外加 `.staging/` 暂存区（`server/app/services/artifact_store.py:46-57`） | legacy 兼容路径：`/api/artifacts` 的本地 CAS 只服务旧版 Worker（逐文件 POST）与存量 blob 读取，新 Worker 产物回传只走 claim 注入的 presigned PUT（对象存储 `jobs-staging/` 前缀，Host HEAD 核验后 promote 到 `jobs/` 权威 key），禁止 POST 到这里（`server/app/routes/artifacts.py:5-6`）。GC 两条路径对存量 legacy blob 仍有效：job 删除时回收其引用过的零引用 blob（`job_artifact_gc.py`）；全库零引用孤儿扫描由周期 orphan GC（默认 1h 一轮，随 sweeper 副本运行，`server/app/services/artifact_orphan_gc.py`）或 `scripts/gc_artifacts.py`（默认 dry-run）执行，删除统一走 `delete_unreferenced` 的事务内 refcount + grace 复查 |
| `agent_bundles/` | `AgentExecutionBroker` / dispatch | 派发给 Worker 的 bundle `<execution_id>.tar.gz`，Worker 回传的结果包 `*.result.tar.gz`（`server/app/agent_broker/dispatch.py` 的 bundle 命名、`server/app/agent_broker/agent_result_commit.py` 的 archive 命名） | 在途传输文件。结果提交后即删除，孤儿文件由 reaper 清扫（`server/app/agent_broker/broker.py` 的 `reap_terminal_bundles` 入口、`server/app/agent_broker/reaper.py` 的孤儿清扫循环） |
| `materials_cache/` | 材料物化缓存（Host 与 Worker 各自一份，Worker 侧在 `{work_root}/materials_cache`） | 内容寻址：`materials_cache/<hash[:2]>/<hash>`（hash 本身即文件名，原始 filename 不进缓存路径），dispatch 时从对象存储（RustFS/S3）流式物化（`shared/material_cache.py`），沙箱静态 allow-read；bundle 条目（文件夹整体一个条目）也物化到同一缓存根下，为确定地址的硬链接目录树 `{cache_root}/{address[:2]}/{address}/{relpath}`（`shared/material_bundle.py`） | 可淘汰缓存，随时可清空（下次 dispatch 重新下载）。容量上限 `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES`（默认 50GiB），超限按 mtime 最旧先删；worker 的 cleanup/stale_sweep 按名字豁免该目录 |

清理节奏由 DB 实例设置（`global_settings` 表 `instance` 文档的 `cleanup` 段：`log_retention_days`、`run_dir_retention_days`、`interval_seconds`，admin API `/api/admin/instance-settings` 维护）控制，加载逻辑见 `server/app/services/log_cleanup.py:21-34`。

所有落盘路径都经过 `server/app/storage_paths.py` 的 `resolve_data_path` / `resolve_managed_path` 约束，保证存储路径不会逃出各自 managed root；受管理的顶层类别为 `videos`、`jobs`、`logs`、`packages`（`server/app/storage_paths.py` 的 `_MANAGED_CATEGORIES`）。

## 2. Worker 侧目录

Worker 不读写 Host 的 `data/`，它持有自己的目录：

| 目录 | 持有者 | 内容 | 生命周期 |
|------|--------|------|----------|
| work root | Worker 执行进程 | 每次执行一个 execution dir，内含解包的 bundle、执行产物与结果 | 可删除缓存/在途状态。配置项 `work_root`，默认 `/var/lib/agent-legion-worker`（`worker/executor.py` 的 `work_root` 解析、`config/agent-worker.example.yaml` 的 `work_root` 项）。supervisor 启动时 `clean_work_root` 清掉崩溃残留目录，但带 `upload_pending.json` 标记的目录保留到结果上报完成（`worker/cleanup.py` 的 `clean_work_root`、`worker/upload_queue.py` 的 `PENDING_FILENAME`） |
| 状态目录 | Worker Service（控制面） | 导入后的可写 `worker.yaml`、`control_token`（0600）、`register_token`、运行状态与指标缓存（`worker/config_store.py` 的 `ConfigStore.save` / `load`） | 持久配置状态，不可随意删除。容器内为 `--state-dir /var/lib/agent-legion-worker-control`（`Dockerfile` 的 worker service `CMD`）；本地运行默认 `data/agent-worker-service`（`worker/cli_args.py` 的默认 state-dir、`worker/service.py` 的本地默认值），即落在仓库 `data/` 下 |
| `bin/` | Worker 自带二进制（裸机/开发部署） | 按平台构建的 velites 副本 `bin/velites` + `bin/velites.src-stamp` 指纹文件（`scripts/ensure-velites.sh --dest data/bin` 安置） | 部署产物，可由脚本按指纹重建。Worker 二进制解析顺序：自带副本优先、PATH 兜底（`worker/binary_resolution.py::resolve_binary`）；Docker worker 镜像内置 velites，不需要此目录 |

`upload_pending.json` 是 UploadQueue 的持久化标记：任务入队前写入 execution dir，Host 接受结果后才删除；Worker 重启时按标记恢复未上报的结果（`worker/upload_queue.py:1-17`）。

## 3. 部署形态映射

- `deploy/compose.host.yaml`：Host 服务设 `AGENT_LEGION_DATA_DIR=/var/lib/agent-legion` 并挂载命名卷 `host-data`；同机 Worker 挂 `worker-data` → `/var/lib/agent-legion-worker`、`worker-control` → `/var/lib/agent-legion-worker-control`（见 `deploy/compose.host.yaml` 的 `volumes` 段）；PostgreSQL 数据在独立卷 `postgres-data`，本地 RustFS 对象存储数据在 `rustfs-data` 卷。
- `deploy/compose.worker.yaml`：独立部署的 Worker 只挂 `worker-data` 与 `worker-control` 两个卷（见 `deploy/compose.worker.yaml` 的 `volumes` 段）。

## 4. 多 worktree 隔离

- 每个 worktree 使用独立的 `data/` 目录与独立端口，互不覆盖运行时状态（`AGENTS.md` 第 1 节；`docs/architecture/project-structure.md` 的多 worktree 约定段）。
- `data/` 下**全部**内容都是每 worktree 独立的：运行时状态、产物、日志、缓存都不跨 worktree 共享。需要非默认位置时用 `AGENT_LEGION_DATA_DIR` 显式指定。
- 本地运行 Worker Service 时，其状态目录默认在 `data/agent-worker-service/`，同样随 worktree 隔离。

## 参考

- `server/app/settings.py` 的 data 根解析与受管子目录创建
- `server/app/storage_paths.py` — managed root 路径约束与 `jobs/`、`logs/` 结构
- `server/app/jobs/storage_layout.py` — job 目录分片布局（shard 计算与新旧布局探测）
- `server/app/services/log_cleanup.py`、DB 实例设置 `cleanup` 段 — 日志与 run dir 保留策略
- `server/app/services/artifact_store.py`、`server/app/agent_broker/broker.py` — `artifacts/` 与 `agent_bundles/`
- `worker/executor.py`、`worker/cleanup.py`、`worker/upload_queue.py`、`worker/config_store.py` — Worker work root 与状态目录
- `deploy/compose.host.yaml`、`deploy/compose.worker.yaml` — 容器卷映射
