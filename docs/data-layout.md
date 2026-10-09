# data/ 目录布局

`data/` 是 Agent Legion 的运行时数据根目录（已 gitignore，禁止提交）。本文从代码推导布局：各子目录由哪个组件持有、内容是什么、生命周期如何。某个实例 `data/` 下实际存在的目录不作为布局依据。

**不要按「本文没列出」删除 `data/` 下的东西。** 其中有不可再生的持久状态（Worker 控制面状态目录里的 control token、注册 token 与生效配置，原生 prod 的运行态记录），也有运维脚本、本地工具或个人放进去的文件。清理前先确认用途：下表标为「可淘汰缓存」「可重新生成」的条目可以删；标为「持久状态」的不要删；未列出的条目先查清来源（`git grep` 路径名、看修改时间）再决定。

数据根的位置默认是仓库下 `data/`，由环境变量 `AGENT_LEGION_DATA_DIR` 覆盖（`config/app.yaml` 的 `data_dir` 键已退役；`server/app/settings.py`）。Host 启动时确保 `data/` 及其下 `videos/`、`logs/`、`packages/`、`jobs/` 存在（`server/app/settings.py`）。

## 1. Host 侧子目录

| 子目录 | 持有者 | 内容与结构 | 生命周期 |
|--------|--------|------------|----------|
| `jobs/` | Workflow 运行时（agent + code 两类节点） | Job 运行产物：`jobs/<workspace>/<shard>/<job_id>/runs/<node_key>/<token>/`，其中 `<shard>` 为 `sha1(job_id)` 前 2 位 hex（`server/app/jobs/storage_layout.py`，**分片仅本地文件系统布局**；对象存储侧的 S3 key 在 `jobs/{workspace_id}/{job_id}/` 前缀下，见下列生命周期），token 目录下含 `session/` 与 Pi 事件流等执行产物（`server/app/executors/_path_canonicalization.py` 的 `canonicalize_finish_paths` 归一 run/session 路径）。旧扁平布局 `jobs/<workspace>/<job_id>/` 只读兼容：读取一律经 `jobs.storage_dir` 列解析，不搬迁不回填 | 产物权威副本在实例对象存储：#853 起每次写入落不可变版本 key `jobs/{workspace_id}/{job_id}/.v/{version}/{name}`（同名重登记改指新 key、被取代对象随即删除，直连 URL 因此固定到签发时的版本；存量行保留 #853 前的 `jobs/{workspace_id}/{job_id}/{name}`，不迁移；与本地分片目录是两个独立命名空间，`server/app/services/job_artifact_objects.py` / `job_artifact_versions.py`，设计见 [artifact-direct-url-pinning.md](architecture/artifact-direct-url-pinning.md)）+ `job_artifacts` 清单表。本地 job_dir 只是执行暂存与可淘汰缓存：保留期清理走 `cleanup_old_logs`（`server/app/services/log_cleanup.py:51`），按 `cleanup.run_dir_retention_days`（默认 3 天）清理过期 run dir、每个节点只保留最新一次 run；另有容量淘汰（`AGENT_LEGION_JOB_CACHE_MAX_BYTES`，默认 50GiB，`server/app/services/job_artifact_maintenance.py`），只删清单已确认的文件。读路径本地命中直读、缺失回退对象存储（EXEC-ARTIFACT-STORE-001）。S3 侧孤儿对象（行已删但对象删除失败、promote 中途失败、staging 残留）由 `scripts/gc-s3-jobs.py` 回收（#340，默认 dry-run） |
| `logs/` | 节点执行日志 + workflow worker；本地进程日志 | 节点日志 `logs/jobs/<job_id>-<node_key>.log`（`server/app/services/job_run_dir_probe.py` 的 `derive_run_dir_from_log_path`）、`workflow_worker_pass.log`（`server/app/workflow_worker/pass_log.py`）。同目录还有本地运行的进程日志：`dev-{backend,frontend,worker}.log`（`scripts/dev_stack.sh`）、`prod-{backend,worker}.log`（`scripts/native-prod-up.sh`），以及状态目录位于 `data/` 下的 Worker 的滚动日志 `executor-<状态目录名>.log` 与结构化事件 `events-<状态目录名>.jsonl`（`worker/executor_log.py`） | 日志。节点日志按 `cleanup.log_retention_days`（默认 7 天）清理；删除 Job 时日志在 DB 提交后移入 `logs/jobs/.trash/<operation_id>/` 再清除（见下文「Job 删除与 `.trash`」）。dev/prod 进程日志每次启动覆盖写，Worker 滚动日志与事件文件按大小轮转；都可删 |
| `videos/` | 视频内容产物（业务节点自建自用） | 平台仅在启动时创建目录；业务剥离后平台代码不再读写该目录 | 内容产物 |
| `packages/` | Workspace 打包导出 | 导出包 `packages/workspace-<workspace_id>/workspace-jobs-*.zip`（`server/app/services/workspace_package_create.py`、`server/app/services/job_packages.py`） | 导出产物，可重新生成 |
| `artifacts/` | `ArtifactStore` | 内容寻址存储：`artifacts/<digest[:2]>/<digest>`，外加 `.staging/` 暂存区（`server/app/services/artifact_store.py:46-57`） | legacy 兼容路径：`/api/artifacts` 的本地 CAS 服务旧版 Worker（逐文件 POST）与存量 blob 读取。新 Worker 产物回传默认走 claim 注入的 presigned PUT（对象存储 `jobs-staging/` 前缀，Host HEAD 核验后 promote 到 `jobs/` 权威 key）；claim 缺上传规格、直传失败或崩溃恢复重进时回落这里的 CAS POST 旧通道（Host 两种形态都收，`worker/upload/queue.py`），新 Worker 代码不得主动 POST 到这里（`server/app/routes/artifacts.py:5-6`）。GC 两条路径对存量 legacy blob 仍有效：job 删除时回收其引用过的零引用 blob（`job_artifact_gc.py`）；全库零引用孤儿扫描由周期 orphan GC（默认 1h 一轮，随 sweeper 副本运行，`server/app/services/artifact_orphan_gc.py`）或 `scripts/gc_artifacts.py`（默认 dry-run）执行，删除统一走 `delete_unreferenced` 的事务内 refcount + grace 复查 |
| `agent_bundles/` | `AgentExecutionBroker` / dispatch | 派发给 Worker 的 bundle `<execution_id>.tar.gz`，Worker 回传的结果包 `*.result.tar.gz`（`server/app/agent_broker/dispatch.py` 的 bundle 命名、`server/app/agent_broker/agent_result_commit.py` 的 archive 命名） | 在途传输文件。结果提交后即删除，孤儿文件由 reaper 清扫（`server/app/agent_broker/broker.py` 的 `reap_terminal_bundles` 入口、`server/app/agent_broker/reaper.py` 的孤儿清扫循环） |
| `materials_cache/` | 材料物化缓存（Host 与 Worker 各自一份，Worker 侧在 `{work_root}/materials_cache`） | 内容寻址：`materials_cache/<hash[:2]>/<hash>`（hash 本身即文件名，原始 filename 不进缓存路径），dispatch 时从对象存储（SeaweedFS/S3）流式物化（`shared/material_cache.py`），沙箱静态 allow-read；bundle 条目（文件夹整体一个条目）也物化到同一缓存根下，为确定地址的硬链接目录树 `{cache_root}/{address[:2]}/{address}/{relpath}`（`shared/material_bundle.py`） | 可淘汰缓存，随时可清空（下次 dispatch 重新下载）。容量上限 `AGENT_LEGION_MATERIAL_CACHE_MAX_BYTES`（默认 50GiB），超限按 mtime 最旧先删；worker 的 cleanup/stale_sweep 按名字豁免该目录 |
| `studio-mcp-files/` | Studio agent MCP server（`server/app/mcp_server/local_files.py`） | 大文件字节精确编辑的暂存区：`studio-mcp-files/<workspace_id>/` 下的导出快照与待提交文件（`output_path` / `code_path` / `files_path` 参数，见 [studio-agent-mcp.md](studio-agent-mcp.md)）。注意它按 **MCP 进程的工作目录**解析（`<cwd>/data/studio-mcp-files/`），不随 `AGENT_LEGION_DATA_DIR` 移动 | 工作文件。导出不覆盖已有文件，平台不自动清理；确认没有进行中的编辑后可删 |
| `native-prod.state` | 原生 prod 启停脚本（`scripts/native-prod-state-lib.sh`） | `make prod-up`（原生形态）落的运行态记录：后端与 Worker 的启动 PID 与实际 bind/port（#894），`make prod-down` 以它为准定位实例。按仓库根相对路径 `data/native-prod.state` 读写，不随 `AGENT_LEGION_DATA_DIR` 移动 | **持久状态**，实例运行期间不要删：删了 down 只能按当前配置定位实例，改过 bind/port 时会停不到旧实例（[agent-worker-deployment.md §2](agent-worker-deployment.md#2-部署机准备挂载目录)） |

**Job 删除与 `.trash`（#958）。** 删除以 DB 为唯一权威：`JobDeletionService.delete` 在 `lease_guarded_mutation` 事务内只删除 jobs 行（文件 I/O 不进事务、不拉长 job-mutation 锁），提交后另起一个只持 `job-mutation:<job_id>` 锁的短事务，在锁下复核 jobs 行仍不存在，才把 job_dir 与节点日志（只删能从该 job 自身记录精确推导的文件，写入方与删除共用 `server/app/storage_paths.py` 的 `job_node_log_name`：普通日志 `<job_id>-<node_key>.log` 按删除前快照的节点 key 生成；分片日志 `<job_id>-<node_key>-shard-<i>.log` 取自删除事务内（持 job-mutation 锁、删行之前）读取的 `node_runs.log_path` 快照——每次 claim 都先插入带该路径的 node_runs 行、之后才写日志，node_runs 只随 job 删除（rerun / workflow 升级删的是 node_shards），路径须位于 `logs/jobs` 下且文件名等于该行节点 key 按命名函数生成的名字；不列举共享日志目录、不做前缀 glob——job_id 与 node_key 都可含连字符，`{job_id}-*` 会命中兄弟 job 的日志））以同文件系统原子 rename 移入 `jobs/.trash/<operation_id>/`、`logs/jobs/.trash/<operation_id>/`，提交后在锁外删除（`server/app/services/job_deletion_trash.py`）；跨文件系统（EXDEV）不在锁下拷贝，直接留残留。失败点终态：事务失败 → 行与文件都不动；提交成功但移入失败，或进程崩溃于提交与移入之间 → 删除仍成功，残留留在原路径（无自动回收，可手动删除；job id 由 workspace/workflow/source 确定性派生，同源重建的 job 会复用该路径）；移入后删除失败 → 残留留在 `.trash/<operation_id>/`；提交后、移入前同源 job（确定性 id）已被重建 → 整体跳过清理，目录与日志归新 job（其中旧 job 的残余缓存交给常规 retention），绝不移走新 job 的活目录；同时跳过按该 job id 的 artifact 引用 / 对象存储清理与删除事件广播；锁下复核在给出结果前因瞬时 DB 错误失败时无法排除重建，按同样方式保守跳过（只泄漏、不误删）。被跳过清理的旧 job 孤儿：本地 CAS blob 由周期 orphan GC（`server/app/services/artifact_orphan_gc.py`）回收，对象存储侧孤儿对象由 `scripts/gc-s3-jobs.py`（默认 dry-run）回收。日志路径在锁外推导，锁内只复核与 rename；单个路径的文件系统错误（如超长文件名 ENAMETOOLONG）只跳过该路径。残留风险仅剩写入期即共用同一文件名的命名碰撞（如 job `A` 的节点 `x-y` 与 job `A-x` 的节点 `y` 都写 `A-x-y.log`），删除侧无从区分。`.trash` 只装已提交删除的残留，**不提供恢复**（行已不存在，无可恢复的归属）；本模块在移入任何文件前先往 operation 目录写入已提交标记 `.committed-deletion`（内容为 job_id）；workflow worker 的周期维护（`cleanup.interval_seconds`，默认 1h 一轮）只删除**带该标记**且 mtime 早于 24 小时（`DELETION_TRASH_TTL`，不可配置，仅是给在途删除留的宽限）的 operation 条目。无标记的条目——0.7.17 之前 `_restore_paths` 在删除事务回滚、原目录被重建时留下的恢复副本（其 jobs 行仍在，对没有 `job_artifacts` 行的 legacy job 可能是唯一产物副本）、symlink 或手工放入的文件——**永不自动删除**，超龄时记 warning，需人工检查后处理。

清理节奏由 DB 实例设置（`global_settings` 表 `instance` 文档的 `cleanup` 段：`log_retention_days`、`run_dir_retention_days`、`interval_seconds`，admin API `/api/admin/instance-settings` 维护）控制，加载逻辑见 `server/app/services/log_cleanup.py:21-34`。

所有落盘路径都经过 `server/app/storage_paths.py` 的 `resolve_data_path` / `resolve_managed_path` 约束，保证存储路径不会逃出各自 managed root；受管理的顶层类别为 `videos`、`jobs`、`logs`、`packages`（`server/app/storage_paths.py` 的 `_MANAGED_CATEGORIES`）。

## 2. Worker 侧目录

Worker 不读写 Host 的 `data/`，它持有自己的目录：

| 目录 | 持有者 | 内容 | 生命周期 |
|------|--------|------|----------|
| work root | Worker 执行进程 | 每次执行一个 execution dir，内含解包的 bundle、执行产物与结果 | 可删除缓存/在途状态。配置项 `work_root`，默认 `/var/lib/agent-legion-worker`（`worker/executor.py` 的 `work_root` 解析、`deploy/worker.remote.example.yaml` 的 `work_root` 项）；dev 布局由 `make install`（`scripts/install-deps.sh`）种子为相对路径 `data/agent-worker`，即仓库下的 `data/agent-worker/`。supervisor 启动时 `clean_work_root` 清掉崩溃残留目录，但带 `upload_pending.json` 标记的目录保留到结果上报完成（`worker/cleanup.py` 的 `clean_work_root`、`worker/upload/queue.py` 的 `PENDING_FILENAME`） |
| 状态目录 | Worker Service（控制面） | 导入后的可写 `worker.yaml`（唯一生效配置）、`control_token`（0600）、`register_tokens/`（各 workspace 注册 token，0600）、运行状态与指标缓存（`worker/config_store.py` 的 `ConfigStore.save` / `load`）；状态目录不在 `data/` 下时（如容器内）还含 `logs/executor.log` 与 `logs/events.jsonl`（`worker/executor_log.py`） | **持久状态**，不可随意删除：删掉即丢失生效配置、注册 token 与控制令牌。容器内为 `--state-dir /var/lib/agent-legion-worker-control`（`Dockerfile` 的 worker service `CMD`）；本地运行默认 `data/agent-worker-service`（`worker/cli_args.py` 的默认 state-dir、`worker/service.py` 的本地默认值），即落在仓库 `data/` 下 |
| `bin/` | Worker 自带二进制 | 按平台构建的 velites 副本 `bin/velites` + `bin/velites.src-stamp` 指纹文件（`scripts/ensure-velites.sh --dest data/bin` 安置） | 部署产物，可由脚本按指纹重建。Worker 二进制解析顺序：自带副本优先、PATH 兜底（`worker/binary_resolution.py::resolve_binary`）；agent runtime 执行器不进 worker 镜像（issue #381），Docker 部署经 compose 把平台匹配的二进制 bind mount 到 `/app/data/bin/velites`（即镜像内的此目录），裸机经 `ensure-velites.sh` 或 GitHub Release 产物安置。原生形态 `make prod-up` 对 PATH 与此副本两处都做指纹刷新（#831：只刷 PATH 时自带副本优先命中，升级静默失效；安置目标由 `scripts/velites_deploy_plan.py` 从真实 resolver 推导，#835），Worker 与 Host 启动对账在消费角色实际解析到的副本 stamp 滞后仓库指纹时打 WARNING |

`upload_pending.json` 是 UploadQueue 的持久化标记：任务入队前写入 execution dir，Host 接受结果后才删除；Worker 重启时按标记恢复未上报的结果（`worker/upload/queue.py:1-17`）。

execution dir 内 agent 执行的 run 目录（`job/runs/<node_key>/worker/`）除 `events.jsonl`（agent 事件流，上传前经 `shared/pi_events.py` 压缩，保留事件的字符串值与 model_error 归因串同趟经 Worker 侧密钥注册表快照脱敏——`worker/upload/stderr_evidence.py` 的 `secret_snapshot`，#842/#844）与 `session/`、`prompt.md` 外，还可能含 `agent-stderr.log`（#748）：agent 子进程的 stderr 在 spawn 侧合并进 stdout 管道、pump 原样落进 events.jsonl，而压缩 rewrite 会丢弃非 JSON 行——上传准备阶段（`worker/upload/prepare.py`）在同一次扫描里把这部分尾部（保尾，硬上限 8KB，`shared/stderr_tail.py` 的 `STDERR_TAIL_BYTES`；落盘前已脱敏）抢救到该文件，并随 run 目录整体进 result.tar.gz 交付 Host，Host 将其与 `events.jsonl` 一起提升进 job dir 的同一 run 目录（超过 8KB 的成员视为不可信、不提升）；进程非零退出（非 130 取消、非 124 超时）时 error_message 与 result metadata 的 `agent_stderr_tail` 字段同步携带该尾部，用于崩溃归因。

## 3. 部署形态映射

- `deploy/compose.host.yaml`：Host 服务设 `AGENT_LEGION_DATA_DIR=/var/lib/agent-legion` 并挂载命名卷 `host-data`；同机 Worker 挂 `worker-data` → `/var/lib/agent-legion-worker`、`worker-control` → `/var/lib/agent-legion-worker-control`（见 `deploy/compose.host.yaml` 的 `volumes` 段）；PostgreSQL 数据在独立卷 `postgres-data`，本地对象存储数据在 `seaweedfs-data` 卷（默认后端；rustfs 逃生舱为 `rustfs-data` 卷）。
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
- `worker/executor.py`、`worker/cleanup.py`、`worker/upload/queue.py`、`worker/config_store.py` — Worker work root 与状态目录
- `deploy/compose.host.yaml`、`deploy/compose.worker.yaml` — 容器卷映射
