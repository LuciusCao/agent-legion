# 部署与配置

## Overview

Agent Legion 使用 PostgreSQL 作为唯一控制面数据库；开发机和生产环境使用同一数据库语义。

## Directory Structure

```
config/
├── skills.yaml               # 外部 Pi skill 源声明
├── skills.lock               # 解析后的 skill commit 锁定
├── agent-worker.example.yaml # Agent Worker 配置模板
└── architecture/             # 架构治理配置（不变量、豁免、体积预算）

# 运行时 split 配置（app.yaml / workflow.yaml / agent_legion.yaml）已整体退役：
# 代码默认值 + env 覆盖 + DB 实例设置文档，文件存在即启动报错（带迁移指引）。

data/                       # 文件产物（gitignored）
├── videos/                 # 下载的视频与产物
├── jobs/                   # Workspace Job 产物
├── packages/               # ZIP 输出
└── logs/                   # 处理日志

scripts/
├── check-quick.sh          # 快速质量门
├── check.sh                # 完整质量门
└── verify_specs.py         # Spec 健康检查
```

## Data Flow

```
开发者启动后端（uvicorn 8000）+ 前端（vite 5173）
    → 前端通过 Vite proxy 访问后端 API
    → 后端通过 PostgreSQL 协调任务，并读写 data/ 目录产物
    → 流水线产物存入 data/videos/{video_id}/
```

生产构建时，前端 `npm run build` 输出到 `frontend/dist/`，由 FastAPI 静态文件中间件托管。

## Key Decisions

- 使用 `uv` 而非 `pip`/`poetry`，依赖锁定在 `uv.lock`。
- PostgreSQL 是唯一运行时数据库；`server/` 与 `scripts/` 已无任何 SQLite 使用，仅 `tools/content-uploader` 用 SQLite 记录自身上传状态。
- 质量门分三层：本地 pre-push 默认 smoke 级（`scripts/run-local-gate.sh`，由 `.githooks/pre-push` 调用）；本地完整门 `check.sh`（`AGENT_LEGION_GATE_LEVEL=full` 触发）；CI（`.github/workflows/quality-gate.yml`）分阶段调用 `scripts/check-quick-backend.sh` / `check-quick-frontend.sh`，不调用 `check.sh`。
- 多 worktree 开发时，每个 worktree 使用独立的后端端口和 `data/` 目录。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 顶层配置项

_全部运行时配置段已从 split yaml 退役：业务参数在 capability config_schema（Studio 节点/workspace 配置覆盖），实例级调参在 DB 实例设置文档（/api/admin/instance-settings），机器路径与密钥走 env（如 AGENT_LEGION_ASR_* / AGENT_LEGION_DATABASE_URL）。_

<!-- END AUTO-GENERATED -->
