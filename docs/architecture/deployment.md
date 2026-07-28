# 部署与配置

## Overview

Agent Legion 使用 PostgreSQL 作为唯一控制面数据库；开发机和生产环境使用同一数据库语义。

## Directory Structure

```
config/
├── app.yaml                  # PostgreSQL、应用路径、HTTP 设置、清理、监控、token 定价
├── agent_legion.yaml         # ASR、CMS、资源提供方、OpenClaw 配置
├── workflow.yaml             # Workspace 执行器与工作流运行时开关
├── skills.yaml               # 外部 Pi skill 源声明
├── skills.lock               # 解析后的 skill commit 锁定
├── agent-worker.example.yaml # Agent Worker 配置模板
└── workflows/                # Workflow DAG 定义（video_knowledge、question_comprehension_info）

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
- PostgreSQL 是唯一运行时数据库；SQLite 只由一次性离线导入器读取。
- 质量门分为 `check-quick.sh`（日常）和 `check.sh`（提交前）。
- 多 worktree 开发时，每个 worktree 使用独立的后端端口和 `data/` 目录。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 顶层配置项

- `agent_workers`
- `agents`
- `asr` — ASR 提供商配置（whisper / SenseVoice）
- `cleanup`
- `cms` — CMS 集成配置
- `data_dir` — 数据目录
- `database`
- `executors` — Workspace 执行器定义
- `heartbeat_failure_threshold`
- `heartbeat_interval_seconds`
- `lease_ttl_seconds`
- `monitoring`
- `openclaw` — OpenClaw 命令模板与工作目录
- `resource_providers` — 资源提供方声明（path/url_key 及各自可调参数的 config_schema，含 secret 标记）
- `server` — HTTP CORS 策略（监听地址由启动命令 --host/--port 决定）
- `token_usage`
- `workflows` — Agent Legion DAG 工作流开关

<!-- END AUTO-GENERATED -->

## Related Specs

- [质量门渐进建设](../superpowers/completed/2026-05-28-quality-gates-design.md)
