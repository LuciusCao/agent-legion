# Agent Legion 架构文档

本目录存放 Agent Legion 的**统一架构文档**，描述当前系统的模块划分、数据流和关键设计决策。

## 现行文档（描述当前系统状态）

| 模块 | 文档 | 职责 |
|------|------|------|
| 后端 | [backend.md](backend.md) | FastAPI 服务、数据库、外部服务连接、配置治理 |
| 前端 | [frontend.md](frontend.md) | React SPA、状态管理、UI 组件 |
| 部署 | [deployment.md](deployment.md) | 本地运行、配置、质量门 |
| 质量门 | [local-quality-gates.md](local-quality-gates.md) | 本地 hooks + GitHub Actions CI 的门禁层级、凭证与分支保护策略 |
| 项目结构 | [project-structure.md](project-structure.md) | 完整目录树 |
| velites harness | [velites-harness.md](velites-harness.md) | 自研 Rust agent harness（velites 执行内核）设计 |
| 证据矩阵 | [workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md) | 架构承诺的反向审计证据矩阵（与 `config/architecture/` invariant registry 对齐） |
| 节点 SDK / Worker 执行 | [node-sdk-and-worker-execution-design.md](node-sdk-and-worker-execution-design.md) | 节点 SDK（NodeContext）与 code 节点执行迁移 Worker 的合并设计（Issue #30/#82） |

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
| [velites-runtime-promotion.md](velites-runtime-promotion.md) | velites 升格为一级 runtime 的实施计划（已落地） |
| [velites-poc-report.md](velites-poc-report.md) | 时点报告（2026-07-31）：pi_agent_rust 替换 Node Pi CLI 的 PoC 验证 |
| [velites-m2-validation.md](velites-m2-validation.md) | 时点报告（2026-07-31）：velites 与 Node pi 真 gateway 对照验证 |
| [risk-review-2026-06-13.md](risk-review-2026-06-13.md) | 2026-06-13 时点架构风险快照 |
| [risk-review-2026-07-18.md](risk-review-2026-07-18.md) | 2026-07-18 架构 Review：扩展性、可维护性与分布式演进路线 |
