# 部署与配置

## Overview

Video Hive 设计为**本地运行**的工具，不依赖云服务。开发者通过 `uv` 管理 Python 依赖，`npm` 管理前端依赖。

## Directory Structure

```
config/
└── pipeline.yaml           # ASR 配置、OpenClaw 命令模板、流水线开关

data/                       # 运行时数据（gitignored）
├── video_hive.sqlite       # SQLite 数据库
├── videos/                 # 下载的视频与产物
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
    → 后端读写 data/ 目录
    → 流水线产物存入 data/videos/{video_id}/
```

生产构建时，前端 `npm run build` 输出到 `frontend/dist/`，由 FastAPI 静态文件中间件托管。

## Key Decisions

- 使用 `uv` 而非 `pip`/`poetry`，依赖锁定在 `uv.lock`。
- 质量门分为 `check-quick.sh`（日常）和 `check.sh`（提交前）。
- 多 worktree 开发时，每个 worktree 使用独立的后端端口和 `data/` 目录。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 顶层配置项

- `asr` — ASR 提供商配置（whisper / SenseVoice）
- `cleanup_video_after_assemble`
- `cms`
- `data_dir`
- `openclaw` — OpenClaw 命令模板与工作目录
- `pipelines` — Agent Legion DAG 流水线开关
- `resource_providers`
- `server`
- `worker`

<!-- END AUTO-GENERATED -->

## Related Specs

- [质量门渐进建设](../superpowers/completed/2026-05-28-quality-gates-design.md)
