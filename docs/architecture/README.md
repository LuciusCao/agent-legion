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
