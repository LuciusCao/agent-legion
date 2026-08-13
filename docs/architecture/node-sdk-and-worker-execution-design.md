# 节点 SDK 与 code 节点执行迁移 Worker（合并设计）

状态：批次 0/1 已实现；批次 2 已实施（§7，协议 v2 + schema v38，2026-08 落地）；
批次 3 已取消（§9）
日期：2026-08-12
关联：Issue #30（code 节点 Host→Worker）、Issue #82（节点 SDK）、
EXEC-CODE-001/002/003、CONFIG-MANIFEST-001、VAULT-SECRET-001、
`custom-workflow-nodes-design.md` §7

## 1. 为什么两个 Issue 一起设计

- #30 要把 code 节点执行从 Host（控制面）迁到 Worker（数据面），前提是节点契约
  收敛为「payload + job_dir + 声明的 config」，不持有 `job_db` 等 Host 内脏。
- #82 要把 9 个内置节点的重复脚手架收敛为节点 SDK（`NodeContext`）；其最难子问题
  「SDK 如何分发进执行环境、版本如何兼容」与 #30 的「节点代码如何在 Worker 上
  运行」是同一个问题。
- 分开做的代价：先迁 Worker 则 SDK 缺少真实约束必然返工；先做 SDK 不管 Worker 则
  可能把 Host 依赖留进 SDK 表面，迁移时再破一次契约。

结论：一次设计、分批落地。批次 1 先做 SDK 与契约收敛（Host 内完成，行为等价）；
批次 2 扩展 Worker 执行协议；批次 3 演进部署形态（Host 内嵌 Worker 为默认）。

## 2. 现状与关键事实（2026-08 盘点）

执行链路：dispatch 解析 node_config（含连接注入，`dispatch_config.py`）与
node_code（`services/node_codes.py`）→ `CodeExecutor.execute`
（`server/app/executors/code.py`）→ 两条路径：

| | 内置节点 | 自定义节点 |
|---|---|---|
| 代码来源 | `workflow_nodes/*.py`（git，EXEC-CODE-001） | DB `workflow_node_codes`（EXEC-CODE-002） |
| 隔离 | 裸 multiprocessing 子进程 | velites `sandbox wrap`，fail-closed（EXEC-CODE-003） |
| runtime | 含 `_job_db_path/_jobs_dir`，子进程重建 `job_db` | 剥离 DB 句柄，父进程预取 `job_batch` |

关键事实：

1. **runtime 两条路径不一致**：`job_db` 是内置路径独有的 Host 依赖，消费点只有
   两处——`question_intake` 读 batch payload（`job_db.get_batch`）、
   `comprehension_assemble/finalize` 经 `collect_skill_versions` 读
   `list_node_runs`。两者都可以在父进程预取，沙箱路径已经这么做了（batch）。
2. **SDK 分发不是新问题**：沙箱 read allowlist 已含 `workspace_libs/` 且
   PYTHONPATH=repo 根（`_code_sandbox.py`），Worker 也跑同一 repo checkout。
   SDK 落在 `workspace_libs/` 下，内置/自定义/沙箱/Worker 四个执行环境今天就能
   import，无需新的下发机制。
3. **auth 失败上报是隐式 Host 依赖**：`report_node_auth_failure(runtime)` 从
   `runtime["job_db"]` 解析 DSN 去失效连接 token 缓存。去掉 `job_db` 必须给这条
   回通道一个替代，否则内置节点行为回退。
4. `configured_skill_fallbacks` 读 `context["settings"]`，而 code executor 的
   runtime 从不含 `settings` 键——生产上是死路径（fallback 恒为 `{}`），
   skill versions 实际全部来自 `list_node_runs`。

## 3. 目标契约：节点只依赖「预取输入 + job_dir + config」

批次 1 完成后，内置与自定义路径的 runtime 键集合完全一致：

```
job_dir, log_path, inputs, expected_outputs, capability, node_key,
workflow_key, execution_id, workspace_id, workspace, job,
settings_config, node_config, cancellation,
job_batch        # 父进程预取（有 batch_id 且父进程有 DB 时）
skill_versions   # 父进程预取（node_key -> skill_version）
```

移除：`_job_db_path` / `_jobs_dir` / 子进程 `job_db` 重建。

特权动作全部留在父进程（控制面）：节点只记录事实，父进程执行。这正好覆盖
auth 失败上报（§5.3）。

## 4. 节点 SDK：`workspace_libs/node_sdk.py`

### 4.1 分层约束

- SDK 只依赖 stdlib（+ 同包 `workspace_libs`），**禁止 import `server.app.*`**。
  这是批次 2 Worker 侧复用的根本：SDK 是执行面代码，不能拖拽控制面依赖。
- 节点可以继续 import 业务库（`server.app.pipeline.*`、`workspace_libs.cms.*`
  等）——业务库不是脚手架，是否随批次 2 向 `workspace_libs/` 搬迁另行评估。
- executor 入口签名不变：`run(job, job_dir, runtime)`。SDK 是节点内部的
  适配层，不是新的执行协议；存量已冻结的自定义节点版本零影响。

### 4.2 API 表面（v1）

```python
from workspace_libs.node_sdk import NodeContext

def run(job, job_dir, runtime):
    ctx = NodeContext(job, job_dir, runtime)

    ctx.job                      # job dict（只读约定）
    ctx.config                   # node_config（dispatch 已合并 defaults/workspace/vault/连接注入）
    ctx.service_config(section=None, legacy_keys=())
                                 # 统一「settings 段打底 + 连接注入 + 节点覆盖 +
                                 #   空值过滤 + legacy 键让步」合并，取代
                                 #   _cms_config ×2 与 _asr_config
    ctx.artifacts.read_json(name) / read_json_object(name)  # 后者校验 dict
    ctx.artifacts.write_json(name, payload)  # 自动 mkdir、统一 ensure_ascii=False indent=2
    ctx.artifacts.read_text(name) / write_text(name, text)
    ctx.checkpoint()             # 取消检查；write_json/write_text 写前自动 checkpoint
    ctx.logger                   # 按 node_key 命名的 logger
    ctx.batch                    # 预取的 batch 行（取代 job_db.get_batch）
    ctx.skill_versions           # 预取的 node_key -> skill_version
    ctx.workflow_manifest(default_key="")  # 原 workflow_manifest(job, ...) 的内容
    ctx.report_auth_failure()    # 见 §5.3
```

取消语义：显式 `ctx.checkpoint()` 保留给长循环；框架在 artifact 写边界自动
checkpoint（写是最常见的「阶段提交点」，9 个节点全有写、只有 3 个有手工检查）。
`checkpoint()` 对 runtime 里的 token 鸭子类型调用 `raise_if_cancelled()`，
SDK 不自定义异常类型，builtin 子进程与沙箱 child 的两种 token 都兼容。

## 5. executor 契约收敛（`server/app/executors/code.py`）

### 5.1 预取上移

`_code_runtime.build_runtime` 统一预取（父进程持有 `job_db`，两条路径共用一份逻辑）：

- `job_batch`：`job["batch_id"]` 存在时 `job_db.get_batch(...)`（现沙箱路径逻辑
  上移，`_code_sandbox.py` 删除重复预取）；
- `skill_versions`：`job_db.list_node_runs(job_id)` 收集
  `node_key -> skill_version`。时序与现状等价（节点 run() 起点 ≈ 预取点，assemble
  类汇总节点在 DAG 中本就排在 agent 节点之后）；
- 删除 `_job_db_path/_jobs_dir` 注入与 `_run_code_node` 里的 `job_db` 重建。

`collect_skill_versions` 随之失去调用方（节点改用 `ctx.skill_versions`），
该模块退役删除；`skill_version_fallbacks`（`configured_skill_fallbacks` /
`job_node_fallbacks`）本批不动——它在生产路径已是死代码但有独立测试，是否
清理单独评估，不与本批耦合。

### 5.2 行为保持

- 预取失败（DB 抖动）语义与现状对齐：batch 预取失败按现状抛错（沙箱路径现状）；
  skill versions 预取失败降级为 `{}`（现状 `collect_skill_versions` 对
  `list_node_runs` 异常即吞掉返回 fallback，生产等价 `{}`）。

### 5.3 auth 失败上报：节点记录事实，父进程执行特权动作

- 节点侧：`ctx.report_auth_failure()` 在 `job_dir/.node_runtime/auth_failure`
  写标记文件（内容为 node_config 里的 connection key，可为空串）。job_dir 在
  内置子进程、沙箱（可写白名单含 job_dir）、未来 Worker 三条路径都可写，无需
  协议扩展；`.node_runtime/` 是目录，不会被 `video_package` 的顶层文件清单
  （`iterdir()` + `is_file()`）捞走。
- 父进程侧：`_execute_isolated` 与 `execute_custom_sandboxed` 在子进程退出后
  检查标记；存在则用 `ConnectionTokenService` 失效该连接的缓存 token，随后删除
  标记。运行前先做 stale 清理（与 result 文件「上次残留不得伪装成功」同理）。
- 语义变化：失效动作从「节点运行中立即」变为「节点退出后、dispatch 前」——
  连接 token 缓存在下一次 dispatch 才被读取，行为等价。自定义沙箱节点从
  「无法上报」变为「可上报」，属能力补齐。

## 6. 内置节点迁移（批次 1 dogfood）

9 个节点全部迁到 SDK，删除重复脚手架：

- `_cms_config` ×2（`question_intake`、`video_download`，仅 legacy 键列表不同）
  → `ctx.service_config(legacy_keys=...)`；
- `_asr_config`（`video_transcribe`）→ `ctx.service_config(section="asr")`；
- `_load_video_input` ×3 → `VideoKnowledgeInput.from_mapping(ctx.artifacts.read_json(...))`
  一行；
- 手写 manifest dict 中的 `collect_skill_versions` / `workflow_manifest` →
  `ctx.skill_versions` / `ctx.workflow_manifest()`；
- 散点 `check_cancellation` → `ctx.checkpoint()` + 写边界自动 checkpoint
  （video_download / video_transcribe 等长操作节点补上显式 checkpoint）；
- `comprehension_common`（question_id 校验等业务 helper）保留，仍由节点 import。

业务逻辑零改动；迁移前后节点输入输出 artifact 逐字节等价（统一 JSON 序列化参数
本就与现状一致）。

## 7. 批次 2 方案：Worker code 执行协议（2026-08-12 已定案，2026-08 已实施）

实施记录见各 chunk（C1–C5）交接报告与 git 历史；本节保留定案方案，落地细节
以代码与 `config/architecture/architecture-invariants.yaml` 为准。

### 7.1 协议形态：复用 agent claim 通道 + 按 kind 分容量池

- 在同一 claim 协议里加 `kind: "code"`，复用 bundle 分发、artifact staging、
  状态回报、心跳与取消通道。manifest 的 code 负载为独立 section（与 agent
  负载构成 tagged union）：`capability`、解析后 `node_config`、代码文本 +
  内容哈希、`expected_outputs`；inputs 走 artifact staging，不进 manifest 本体。
- 并发隔离靠**容量池**而非通道数量：Worker 声明容量时按 kind 分开（agent/code
  各自上限），Host 分开记账、分开强制（机制同现有 `max_concurrency` 的 claim
  检查，`server/app/agent_broker/claim.py`）。长 code 任务（如转录）只占 code
  池，不拖慢 agent；code 池内部快慢细分留待后续。

### 7.2 节点代码分发：Host 发送，放弃 git 指纹

- 内置与自定义节点统一走「代码文本 + 内容哈希随任务下发」：Host 在 dispatch
  时解析代码（内置读 repo 文件，自定义读 DB frozen 版本，解析序同
  `resolve_dispatch_node_code`）。Worker 零 repo 依赖，只需 Python + velites
  二进制。原 git 指纹握手方案（Worker 本地 checkout + 漂移拒领）已放弃。
- 前提：节点自足——只能 import `workspace_libs` + stdlib（外加 Worker 镜像预装、
  沙箱内可 import 的 `requests`），与自定义节点沙箱规则对齐，可用静态检查强制
  （`server/app/agent_broker/code_eligibility.py`，按 code_hash 缓存）。
  EXEC-CODE-001 不变：内置代码源头仍是
  Host 的 git 仓库，评审链不变，仅运输方式从「Worker 读本地 repo」变为
  「Host 读了发过去」。
- 批次 2a 前置：把节点依赖的轻量 helper（`comprehension_common`、
  `comprehension_contract`、`question_fingerprint`，均为纯 stdlib）下沉到
  `workspace_libs`。下沉后 6 个节点自足（`question_intake`、`question_clean_parse`、
  `comprehension_classify` / `comprehension_assemble` / `comprehension_finalize`、
  `video_package`），走 Host 发送上 Worker。
- 3 个 video 重节点（`video_download` / `video_assemble` / `video_transcribe`，
  依赖 `server.app.pipeline.*`：ffmpeg 子进程、ASR provider、第三方库
  `requests`/`srt`）批次 2 暂缓上 Worker，留 Host 本地执行（routing 不绑定
  Worker 即可）。后续单独决策：`pipeline` 下沉 vs 视频处理专用 Worker——
  其依赖（ffmpeg、ASR 模型路径）与机器环境强绑定，值得独立设计。
- 安全加分：Worker 上所有 code 执行统一过 velites 沙箱（内置节点在 Host
  本地本不沙箱，远程化后反而更规范）。

### 7.3 secret 下发边界（VAULT-SECRET-001 的延伸）

- 传输仅走既有 HTTPS 通道，不开明文旁路。
- Worker 侧仅内存驻留：不落盘、不进日志。落地时必须检查 Worker 的
  manifest/bundle 持久化路径，必要时在落盘前剔除 secret 键。
- 一期不做按节点白名单：节点只能拿到自身 config_schema 声明的连接键，已是
  事实上的最小下发；白名单收束留二期。

### 7.4 执行面共享包位置

- sandbox child（`_code_child` 等价物）与 SDK 收敛到 `workspace_libs/`：
  `_code_child` 对 `server.app` 仅两个依赖（`CancellationToken`、
  `_load_run_from_source`），随之下沉后零依赖；`workspace_libs/` 已在沙箱
  allowlist，不需要动沙箱策略。
- velites wrap argv 构建与进程管理留在执行器层，Host/Worker 各一份（Worker
  侧复制进 `worker/`），批次 3 时 Host 侧那份删除。

### 7.5 取消信号

- 复用 Worker 轮询/回报通道，Host 回复中携带显式取消字段，不设独立通道；
  Worker 收到后 kill 进程组（SIGTERM），沙箱 child 的 token 语义不变。
- 时延目标一个轮询周期（秒级），对分钟级 code 节点足够。

### 7.6 SDK 版本兼容策略

- SDK 随任务从 Host 下发（`workspace_libs` 快照 + 内容哈希），Worker 不需要
  本地副本，SDK 版本始终与 Host 一致。
- 自定义节点冻结的是代码文本，不冻结 SDK 版本：SDK 承诺向后兼容（只加不减，
  破坏性变更走 `NodeContext` 新方法名）。SDK 变更的契约测试
  （`tests/workflow_nodes/test_node_sdk.py` + 沙箱 import 契约测试）是兼容性的
  强制闸。

## 8. 安全边界分析

- 连接 token（明文）今天已随 `node_config` 进入内置子进程与沙箱（仅内存，
  stdin pickle，不落盘）。批次 2 把它送出 Host 进程边界到 Worker：信任级别不变
  （Worker 是数据面，拿任务所需最小输入），但落点扩大，传输必须走既有 TLS
  通道且 Worker 侧不落盘、不进日志（VAULT-SECRET-001 的延伸约束，批次 2 落地
  时在 invariants 补证据）。
- `job_db` 移除后，节点运行时不再有任何直达 DB 的能力；auth 上报标记是纯文件
  事实，父进程重新校验 connection key 来源后才执行失效（§5.3），不存在
  「子进程伪造标记失效他人连接」的越权面（节点本就知道自己用的 connection
  key，失效自己的缓存 token 不产生新权限）。

## 9. 批次 3：已取消（2026-08-12 决策）

原设想「Host 内嵌 Worker 进程、本地执行也走 claim 协议、Host 摘 velites 依赖」，
经评审后取消，理由：

- 批次 2 已把漂移大头解决：SDK、sandbox child、节点代码全部共享（
  `workspace_libs` 收敛 + Host 发送代码文本），剩下的「双实现」只是编排胶水
  （本地直接调用 vs claim 协议），漂移空间很小；
- 批次 2 决定 3 个 video 重节点留在 Host 本地执行，Host 的本地执行路径
  无论如何删不掉，「Host executor 整个删除、摘 velites」不可能全量达成；
- 「单机全功能」部署用同机独立 Worker 进程指向 localhost 即可，是纯部署
  拓扑，由现有部署文档/runbook 覆盖，不需要开发「Host 内嵌进程管理」。

终局定位：Host 本地 code executor = video 三节点专用 + 无 Worker 时的兜底。
若将来 video 节点找到归宿（如视频处理专用 Worker），再重新评估 Host 是否
彻底退出执行。

## 10. Quality Impact

- **测试**：新增 `tests/workflow_nodes/test_node_sdk.py`（SDK 单测）与沙箱内
  import SDK 的契约测试；executor 收敛改动覆盖 `tests/executors/
  test_code_executor.py`、节点测试（`tests/workflow_nodes/`）全量回归；
  auth 上报标记通道新增父进程侧测试。
- **风险点**：预取时序变化（skill_versions 从「节点运行时读」变「执行起点读」）
  ——DAG 保证汇总节点在 agent 节点之后，时序等价；auth 失效动作延后到节点退出
  ——语义等价（§5.3）。
- **回滚**：批次 1 全部为 Host 内行为等价重构，revert 即回滚；无 DB schema
  变更、无 API 变更、无配置变更。
- **覆盖率**：删除重复脚手架净减少生产代码行数，覆盖率只升不降。
