# Video Hive 架构文档

本目录存放 Video Hive 的**统一架构文档**，描述当前系统的模块划分、数据流和关键设计决策。

如需了解历史设计规格（spec）和开发过程，请参阅 [`docs/superpowers/`](../superpowers/)。

## 模块索引

| 模块 | 文档 | 职责 |
|------|------|------|
| 后端 | [backend.md](backend.md) | FastAPI 服务、数据库、CMS 集成、Agent Legion |
| 前端 | [frontend.md](frontend.md) | React SPA、状态管理、UI 组件 |
| 流水线 | [pipeline.md](pipeline.md) | 视频下载、转录、Agent 阶段、打包 |
| 部署 | [deployment.md](deployment.md) | 本地运行、配置、质量门 |
