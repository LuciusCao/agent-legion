# Agent Legion 架构文档

本目录存放 Agent Legion 的**统一架构文档**，描述当前系统的模块划分、数据流和关键设计决策。

## 模块索引

| 模块 | 文档 | 职责 |
|------|------|------|
| 后端 | [backend.md](backend.md) | FastAPI 服务、数据库、CMS 集成、Agent Legion |
| 前端 | [frontend.md](frontend.md) | React SPA、状态管理、UI 组件 |
| 流水线 | [pipeline.md](pipeline.md) | 视频下载、转录、Agent 阶段、打包 |
| 部署 | [deployment.md](deployment.md) | 本地运行、配置、质量门 |
| 质量门 | [local-quality-gates.md](local-quality-gates.md) | 本地 hooks + GitHub Actions CI 的门禁层级、凭证与分支保护策略 |
| 项目结构 | [project-structure.md](project-structure.md) | 完整目录树 |
| 架构风险 | [risk-review-2026-06-13.md](risk-review-2026-06-13.md) | 当前架构问题、风险与处理优先级 |
| 架构风险 | [risk-review-2026-07-18.md](risk-review-2026-07-18.md) | 2026-07-18 架构 Review：扩展性、可维护性与分布式演进路线 |
| velites harness | [velites-harness.md](velites-harness.md) | 自研 Rust agent harness（velites 执行内核）设计 |
| velites runtime | [velites-runtime-promotion.md](velites-runtime-promotion.md) | velites 升格为一级 runtime 的实施计划（已落地） |
| velites PoC | [velites-poc-report.md](velites-poc-report.md) | 时点报告（2026-07-31）：pi_agent_rust 替换 Node Pi CLI 的 PoC 验证 |
| velites M2 验证 | [velites-m2-validation.md](velites-m2-validation.md) | 时点报告（2026-07-31）：velites 与 Node pi 真 gateway 对照验证 |
| 视频迁移 | [video-knowledge-workspace-migration.md](video-knowledge-workspace-migration.md) | 知识视频从旧版视频运行时迁移到 Agent Legion Workspace runtime |
| 证据矩阵 | [workspace-executor-evidence-matrix.md](workspace-executor-evidence-matrix.md) | Phase 1-5 Workspace Executor 架构承诺的反向审计证据矩阵 |
| 节点 SDK / Worker 执行 | [node-sdk-and-worker-execution-design.md](node-sdk-and-worker-execution-design.md) | 节点 SDK（NodeContext）与 code 节点执行迁移 Worker 的合并设计（Issue #30/#82） |
| 节点 SDK 交接 | [node-sdk-and-worker-execution-handoff.md](node-sdk-and-worker-execution-handoff.md) | 批次 0/1 交接：批次 2/3 决策点与 Studio 节点骨架问题 |
