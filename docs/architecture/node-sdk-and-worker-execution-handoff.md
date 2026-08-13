# 交接：节点 SDK / Worker 执行迁移 — 批次 2/3 与 Studio 节点骨架

日期：2026-08-12
关联：Issue #30、Issue #82、设计文档
[node-sdk-and-worker-execution-design.md](node-sdk-and-worker-execution-design.md)、
EXEC-CODE-001/002/003/004

本文档是批次 0/1 完成后的交接材料，供后续重点讨论：批次 2（Worker code 执行
协议）、批次 3（Host 内嵌 Worker）、以及 Studio 无法独立新建节点骨架的问题。

## 1. 已完成（批次 0/1，commit e2ce254c）

- 节点 SDK `workspace_libs/node_sdk.py`（`NodeContext`，仅 stdlib 依赖）；
  9 个内置节点全部迁移，入口签名 `run(job, job_dir, runtime)` 不变。
- runtime 契约收敛：内置与沙箱路径同一组键；`job_batch`/`skill_versions` 父进程
  预取（`server/app/executors/_code_runtime.py`）；`job_db`/DSN 移出节点运行时。
- auth 失败上报改为 marker 通道：节点 `ctx.report_auth_failure()` 只写
  `job_dir/.node_runtime/auth_failure`，父进程验 key 后执行 token 失效。
- 治理：EXEC-CODE-004 已登记（invariants + 证据矩阵）；AGENTS.md §6 已更新；
  budgets 已 ratchet。quick 门全绿（backend 3121 / frontend 1230 / rust）。

## 2. 批次 2：Worker code 执行协议 — 讨论要点

设计文档 §7 给了方案草案，以下是需要拍板的决策点：

### 2.1 协议形态：复用 agent 通道 vs 独立 code 通道

- 现状：Worker 只有 `/api/agent-executions/claim` + bundle/manifest，承载
  pi/velites argv；code 执行需要新的负载类型（代码文本或 repo path、解析后
  node_config、inputs/expected_outputs）。
- 选项 A：在同一 claim 协议里加 `kind: "code"`，复用 bundle/artifact/回报通道
  —— 改动面小，调度与容量语义一致；代价是 manifest schema 要分叉。
- 选项 B：独立 `/api/code-executions/*` 通道 —— 边界清晰；代价是两套路由、
  回报、心跳逻辑。
- 倾向：A（bundle 机制与 artifact staging 都是现成的，复用率最高）。

### 2.2 内置节点代码在 Worker 上的版本一致性

- 内置节点代码取 Worker 本地 repo checkout；Host 的 capability 定义（DB
  versioned_entities）与 Worker 的 git checkout 可能漂移。
- 提议：注册握手交换 git 指纹（commit hash 或 workflow_nodes/ 目录指纹），
  不匹配则 Worker 不领 code 任务。需要定：指纹粒度（整仓 vs 目录）、漂移时的
  行为（拒领 vs 告警）。

### 2.3 secret 下发边界（VAULT-SECRET-001 的延伸）

- 连接注入的 `connection_config` 含明文 token，今天要随任务跨进程到 Worker。
- 需要定：传输（既有 TLS 通道）、驻留（仅内存，不落 Worker 磁盘、不进日志）、
  以及是否引入按节点白名单的最小下发（设计文档 §7 的三期思路）。这是批次 2
  评审的安全重点。

### 2.4 执行面代码的共享包位置

- Worker 侧执行要复用 `_code_sandbox.py` 的 velites wrap 与 `_code_child` 等价物；
  目标是 Worker 不 import `server.app`。
- 需要定：sandbox child + SDK 收敛到 `workspace_libs/`（现成、已在沙箱
  allowlist）还是新独立包；`_code_child` 目前 import `server.app.executors.*`，
  需要拆解的程度。

### 2.5 取消信号

- 沙箱 child 的 token 是本地重建的；Worker 化后取消要跨网络：Worker 轮询/回报
  通道携带取消请求 → Worker kill 进程组（SIGTERM → token 语义不变）。需要定
  取消的传递通道与时延上限。

## 3. 批次 3：Host 内嵌 Worker — 讨论要点

- 「本地跑」= Host 内嵌 Worker 进程，执行路径与分布式完全一致（同一套
  binding → executor、同一套沙箱），区别只是传输从网络变本机。
- Host 侧现有 code executor 演进为内嵌 Worker 模式的实现，消除双实现漂移——
  需要定演进顺序：先并存（feature flag）还是直接切换。
- Host 摘掉 velites 二进制依赖（回到 Worker 侧）；部署文档与
  `remote-execution-runbook.md` 更新。
- 前置依赖：批次 2 的协议必须先稳定，内嵌模式只是同一协议的回环传输。

## 4. Studio 无法独立新建节点骨架

### 现状事实

- 自定义节点 = fork：`workflow_node_codes` 绑定到既有 `(workspace, workflow,
  node_key)`，编辑器起稿内容是 `draft_code ?? 内置代码`
  （`frontend/src/pages/workflowStudio/WorkflowNodeCodeSection.tsx:158`）——
  没有「空白新建」入口，也没有模板。
- 保存校验只有：体积上限、语法合法、存在模块级 `run`
  （`server/app/services/node_codes.py:73-84` `validate_node_code`）——不检查
  签名、不引导 SDK 用法。
- 批次 1 之后起稿代码天然是 SDK 写法（内置节点已迁移），部分缓解了「骨架」
  问题，但 fork 一个 200 行的 `question_intake` 当起点仍然不友好。

### 讨论点

1. **模板骨架**：在节点代码面板提供「从模板新建」——最小 SDK 骨架
   （`NodeContext` 取 config/读写 artifact/checkpoint 的注释示例）。模板放哪：
   前端常量还是后端下发（后端单源更好，可随 SDK 演进）？
2. **「全新节点」的语义**：真正的「新建节点」不止代码——涉及 workflow 定义
   （DAG 加节点）+ capability 声明。本期是否只做「fork 起点优化」（模板），
   把「新 capability + 新节点」留给 agent 生成 workflow 的主线？
3. **校验加强**：`validate_node_code` 是否检查 `run` 签名形态、SDK 可用性
   （沙箱 import 白名单）等，减少发布后才暴露的低级错误？

## 5. 开放问题清单（汇总）

| # | 问题 | 归属 |
|---|------|------|
| 1 | claim 协议加 kind 还是独立 code 通道 | 批次 2 §2.1 |
| 2 | Host/Worker git 指纹握手粒度与漂移行为 | 批次 2 §2.2 |
| 3 | secret 随任务下发的传输/驻留/白名单边界 | 批次 2 §2.3 |
| 4 | sandbox child/SDK 共享包位置 | 批次 2 §2.4 |
| 5 | 取消信号跨网络通道与时延 | 批次 2 §2.5 |
| 6 | 内嵌 Worker 演进顺序（并存 vs 直切） | 批次 3 |
| 7 | Studio 节点模板骨架形态与「新建节点」语义 | §4 |

## 6. 验证与复跑

- 质量门：`./scripts/check-quick.sh`（本批全绿）；CI full gate 以 PR 为准。
- 关键测试：`tests/workflow_nodes/test_node_sdk.py`（SDK 契约）、
  `tests/executors/test_code_executor.py`（含沙箱 SDK import 契约、marker 通道、
  预取）。
