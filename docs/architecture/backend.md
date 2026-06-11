# 后端架构

## Overview

Video Hive 后端基于 FastAPI，提供 REST API 和 SSE 事件推送。核心职责包括：

- 视频队列管理（ intake → 下载 → 转录 → Agent 阶段 → 打包）
- Agent Legion DAG 流水线执行（Workspace / Job / Node）
- CMS 集成（知识库与题库查询）
- SQLite 持久化与本地文件系统管理

## Directory Structure

```
server/app/
├── main.py                 # FastAPI 应用工厂 + 生命周期
├── routes/                 # REST API 路由
│   ├── videos.py           # 视频 CRUD 与批量操作
│   ├── jobs.py             # Agent Legion Job API
│   ├── packages.py         # 打包管理
│   ├── agents.py           # Agent 状态查询
│   └── worker.py           # Worker 控制（暂停/恢复）
├── services/               # 业务逻辑服务层
│   ├── intake.py           # 视频入库
│   ├── video_actions.py    # 批量操作
│   ├── manual_run.py       # 手动阶段运行
│   └── interaction_stats.py# 交互统计
├── pipeline/               # 视频处理流水线阶段
│   ├── download.py         # HTTP 下载
│   ├── transcribe.py       # ASR 转录
│   ├── openclaw.py         # OpenClaw Agent 调用
│   ├── assemble.py         # 元数据组装
│   └── package.py          # ZIP 打包
├── pipelines/              # Agent Legion DAG 定义与执行
│   ├── definition.py       # 流水线定义解析
│   ├── scheduler.py        # DAG 调度
│   ├── executor.py         # 节点执行
│   └── pi_runner.py        # Pi Agent 运行器
├── db/                     # 数据库层
│   ├── schema.py           # 表结构定义
│   ├── queries.py          # 视频相关查询
│   └── notifications.py    # SSE 通知
├── cms/                    # CMS 客户端
│   ├── auth.py             # 认证
│   ├── client.py           # HTTP 客户端
│   ├── knowledge.py        # 知识库查询
│   └── question.py         # 题库查询
├── worker*.py              # 后台工作线程（视频 + 流水线）
└── agents.py               # Agent 发现与状态跟踪
```

## Data Flow

```
客户端请求 → FastAPI Router → Service Layer → DB / Pipeline / CMS
                     ↓
               SSE Events ← DB Notifications
                     ↓
               前端实时更新
```

后台 Worker 线程定期轮询数据库，驱动视频从 `queued` 状态向 `completed` 状态推进。

## Key Decisions

- 使用 SQLite 作为本地数据库，避免外部依赖。详见相关 spec。
- 视频流水线与 Agent Legion 流水线使用独立的 Worker 线程，避免相互阻塞。
- 所有文件 I/O 限制在 `data/` 目录内，由 `security.py` 做路径校验。

## API Surface / Interface

<!-- TODO: 阶段 2 由 AST 自动生成 -->

## Related Specs

- [Worker 轮询性能](../superpowers/completed/2026-05-29-worker-polling-performance-design.md)
- [数据库性能优化](../superpowers/completed/2026-05-29-database-performance-design.md)
