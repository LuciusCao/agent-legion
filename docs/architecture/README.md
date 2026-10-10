# Agent Legion 架构文档

本目录存放 Agent Legion 的**统一架构文档**，描述当前系统的模块划分、数据流和关键设计决策。

## 系统总览

```
Browser (React SPA: 运维控制台 + Studio)
   │ REST / SSE / WebSocket
   ▼
FastAPI Host ─────────────────────────────────────────────────┐
   │ routes → services → workflows (DAG scheduler)            │
   │ studio_chat → ACP agent 子进程 → MCP 工具面（scoped token）│
   │                        │ capacity leases                 │
   ▼                        ▼                                 ▼
PostgreSQL           Implicit code pool             Agent Workers (remote)
(control plane)      workflow code nodes            claim → run → presigned upload
                            │                                 │
                            ▼                                 ▼
        实例对象存储（S3 / SeaweedFS）：job 产物权威副本、材料
        data/：执行暂存与可淘汰缓存、日志、运行痕迹
agent nodes → velites / Pi CLI → skills (local in-place git, pins in DB skill_lock)
```

- **Backend**: Python 3.11+, FastAPI, Uvicorn, PostgreSQL
- **Frontend**: React 18, TypeScript, Vite, TanStack Query, Zustand, MUI v6, XYFlow
- **Agent harness**: velites (Rust, `velites/`) or Pi CLI (Node)
- **Tooling**: `uv` + Ruff + mypy (Python), npm + ESLint + Prettier (frontend),
  pytest + Vitest + cargo test

关键设计规则（由架构检查强制，见仓库根 [AGENTS.md](../../AGENTS.md) 与
[workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md)）：

- Workflow 节点声明 `capability` 与显式执行类型（`type: code | agent`，另有
  `start` / `approval`）。agent 节点的执行档案优先取节点自含的
  `execution.runtime`（workflow 顶层 `execution` 可给默认，EXEC-AGENT-PROFILE-001）；
  未声明 runtime 的 agent 节点在 #440 过渡期内仍按 capability 解析到 published
  Agent 定义。agent 节点可声明 `skill` 内容绑定（`key` + 可选 `ref`，#76）；code
  节点解析到已发布的 `node_code`。
- Route 是薄 HTTP 适配层；业务逻辑在 service；执行容量一律经 lease 申请
  （`server/app/executors/leases.py`；executor 定义/绑定概念已随 schema v47 退役）。
- 前端 transport 类型从后端 OpenAPI schema 生成
  （`frontend/src/generated/api.ts`），禁止手写。
- Secret 只进 vault 或 env —— tracked 配置 yaml 在启动时拒绝 secret 值。

## 现行文档（描述当前系统状态）

| 模块 | 文档 | 职责 |
|------|------|------|
| 后端 | [backend.md](backend.md) | FastAPI 服务、数据库、外部服务连接、配置治理 |
| 前端 | [frontend.md](frontend.md) | React SPA、状态管理、UI 组件 |
| 部署约束 | [deployment.md](deployment.md) | 部署形态的硬约束：Worker 容器特权边界、Host 单副本、浏览器安全头与低权读面；配置来源（部署步骤见 `docs/agent-worker-deployment.md`） |
| 质量门 | [local-quality-gates.md](local-quality-gates.md) | 本地 hooks + GitHub Actions CI 的门禁层级、凭证与分支保护策略 |
| 评审收敛 | [review-convergence.md](review-convergence.md) | 自动评审 finding 的阻塞/非阻塞分诊、缺陷族普查、高风险面对抗式复验与停止条件（#835） |
| 项目结构 | [project-structure.md](project-structure.md) | 仓库目录地图（列到有意义的层级） |
| velites harness | [velites-harness.md](velites-harness.md) | 自研 Rust agent harness（velites 执行内核）设计 |
| 证据矩阵 | [workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md) | 架构承诺的反向审计证据矩阵（与 `config/architecture/` invariant registry 对齐） |
| 执行代次协议 | [execution-generation.md](execution-generation.md) | EXEC-GENERATION-001 执行代次协议（#759/#645）：代次列与 bump/CAS 面、锁序与批序全序、三平面一致性与并发对抗审查 checklist |
| 产物身份状态空间 | [artifact-identity-state-space.md](artifact-identity-state-space.md) | 产物身份协议的网格模型（#876）：生命周期六阶段 × 八变异轴 × 八不变量（EXEC-INPUT-IDENTITY-001 / EXEC-VALIDATION-VIEW-001）逐格钉测试/论证，一致性检查防腐 |
| 产物直连 URL 版本固定 | [artifact-direct-url-pinning.md](artifact-direct-url-pinning.md) | 不可变版本 key 布局与被取代对象清理（#853）：方案对比、SeaweedFS 实测、存量兼容与残余面 |
| Studio 草稿-校验-发布契约 | [studio-draft-publish-contract.md](studio-draft-publish-contract.md) | studio 草稿编辑的三台协作状态机（保存/自动校验/发布）迁移表 + 组合 invariant 表与变更纪律（#633/#804） |
| Studio 本地文件编辑 | [studio-local-authoring-contract.md](studio-local-authoring-contract.md) | Git 内容归属、shared 全量状态、传输预算与测试矩阵（#820） |
| Kimi 后台任务接续 | [studio-kimi-background-wakeup.md](studio-kimi-background-wakeup.md) | 后台终态回执、空闲接续及 Kimi V1 存储兼容边界（#806） |
| Studio 服务生命周期 | [studio-service-lifecycle.md](studio-service-lifecycle.md) | create/resume 准入、在途启动排空与 shutdown 清理顺序（STUDIO-RUNTIME-001） |
| 节点 SDK / Worker 执行 | [node-sdk-and-worker-execution-design.md](node-sdk-and-worker-execution-design.md) | 节点 SDK（NodeContext）与 code 节点执行迁移 Worker 的合并设计（Issue #30/#82） |
| 材料与 runs | [materials-and-runs-design.md](materials-and-runs-design.md) | runs / 材料 / bundle 文件夹条目 / 产物对象存储的输入模型与治理设计 |
| velites 模型注册 | [velites-model-registry.md](velites-model-registry.md) | runtime-owned 模型发现与 velites provider registry（Worker 侧发现、Host 侧三元组路由） |
| 文档治理 | [docs-governance.md](docs-governance.md) | 文档漂移检查（退役术语基线 `docs_retired_terms` + 事实一致性 `docs_consistency`）的机制说明与维护指引 |
| 实例设置旧概念治理 | [instance-settings-legacy-concepts-governance.md](instance-settings-legacy-concepts-governance.md) | `workflows.enabled` 退役与 `code_capacity` 改述（0 = 纯控制面模式）的实施定稿（#385/#386/#389） |

## 历史设计记录（时点快照，仅供溯源）

以下文档是设计定稿、实施计划或时点报告的存档，文中的 `path:line` 证据与
部分结论反映当时代码；与现行语义冲突时以代码、现行文档与
`config/architecture/architecture-invariants.yaml` 为准。各文开头的状态
banner 标注了后续演进对其中结论的修订。

| 文档 | 说明 |
|------|------|
| [custom-workflow-nodes-design.md](custom-workflow-nodes-design.md) | DB-backed 自定义节点代码设计（已实现；path 绑定与 executor 概念后续的退役见文首 banner） |
| [agent-config-governance.md](agent-config-governance.md) | Agent 配置治理定稿（yaml `agents:` / `workflows.pi` 退役，已完成） |
| [agent-config-implementation-plan.md](agent-config-implementation-plan.md) | 上述治理的详细实施计划（已完成） |
| [workflow-studio-evolution-design.md](workflow-studio-evolution-design.md) | Studio 定位（agent authoring + 可视化调优发布台）与阶段路线 |
| [studio-phase3-implementation-plan.md](studio-phase3-implementation-plan.md) | Studio 内置 agent 实施计划（MCP/ACP 三层分离，已落地） |
| [studio-node-type-selector-design.md](studio-node-type-selector-design.md) | Studio 节点类型抽象落地设计：inspector 类型选择器 + 按类型注册 section 集（#392，已实施） |
| [velites-runtime-promotion.md](velites-runtime-promotion.md) | velites 升格为一级 runtime 的实施计划（已落地） |
| [velites-poc-report.md](velites-poc-report.md) | 时点报告（2026-07-31）：pi_agent_rust 替换 Node Pi CLI 的 PoC 验证 |
| [velites-m2-validation.md](velites-m2-validation.md) | 时点报告（2026-07-31）：velites 与 Node pi 真 gateway 对照验证 |
| [risk-review-2026-06-13.md](risk-review-2026-06-13.md) | 2026-06-13 时点架构风险快照 |
| [risk-review-2026-07-18.md](risk-review-2026-07-18.md) | 2026-07-18 架构 Review：扩展性、可维护性与分布式演进路线 |
| [workflow-key-retirement-inventory.md](workflow-key-retirement-inventory.md) | `workflow_key` 退役盘点（issue #211 Phase 1 产出，退役执行的输入清单） |
| [llm-output-budget-design.md](llm-output-budget-design.md) | LLM 节点单次输出预算与触顶续写（#952）：P0 显式输出预算参数 + 触顶归因已落地，自动续写为设计草案（待 owner 决策） |
| [execution-snapshot-retirement-draft.md](execution-snapshot-retirement-draft.md) | `jobs.workflow_definition_snapshot_json` 瘦身（#354 方案 3）的评估结论与迁移草案（设计草案，未实施；草拟的 v72 编号已被占用，见文首补注） |

## Studio 文档导航

Studio（可视化编排 + 内置 agent 对话）的文档分散在上面两张表与 `docs/` 根下，按主题汇总：

| 主题 | 文档 | 性质 |
|------|------|------|
| 草稿-校验-发布状态机 | [studio-draft-publish-contract.md](studio-draft-publish-contract.md) | 现行契约 |
| 本地文件编辑与 Git 内容归属 | [studio-local-authoring-contract.md](studio-local-authoring-contract.md) | 现行契约 |
| 后台任务接续（Kimi） | [studio-kimi-background-wakeup.md](studio-kimi-background-wakeup.md) | 现行契约 |
| 会话服务生命周期 | [studio-service-lifecycle.md](studio-service-lifecycle.md) | 现行契约 |
| MCP 工具面与外部 agent 接入 | [../studio-agent-mcp.md](../studio-agent-mcp.md) | 现行运维/集成文档 |
| 定位与阶段路线 | [workflow-studio-evolution-design.md](workflow-studio-evolution-design.md) | 历史设计记录 |
| 内置 agent（MCP/ACP 三层分离） | [studio-phase3-implementation-plan.md](studio-phase3-implementation-plan.md) | 历史设计记录 |
| 节点类型选择器 | [studio-node-type-selector-design.md](studio-node-type-selector-design.md) | 历史设计记录 |

## 索引约定

索引完整性约定：本目录新增 `.md` 文件必须同时登记进「现行文档」或
「历史设计记录」其中一张表（PR 检查项）；未登记的文件视为索引债。
