# 视频流水线架构

## Overview

视频流水线将单个视频从 URL 处理为 CMS 可导入的 ZIP 包。流程分为 8 个阶段（知识视频）或 6 个阶段（题目解析视频），由后台 Worker 自动驱动。

## Directory Structure

```
server/app/pipeline/
├── phases.py               # 阶段定义与序列
├── download.py             # HTTP 视频下载
├── transcribe.py           # ASR 转录（whisper.cpp / SenseVoice）
├── openclaw.py             # OpenClaw Agent 阶段调用
├── assemble.py             # metadata.json 组装
├── upload_params.py        # upload_params.json 生成
├── package.py              # ZIP 打包
├── reader.py               # 产物读取（API 层使用）
├── artifacts.py            # 产物清理
├── recovery.py             # 中断恢复
└── validators.py           # 输入校验
```

Agent Legion 流水线（与视频流水线独立）：

```
server/app/pipelines/
├── definition.py           # DAG 定义加载
├── scheduler.py            # 下游节点解析
├── executor.py             # 单节点执行
├── pi_runner.py            # Pi Agent 调用
└── skills.py               # Skill 注册
```

## Data Flow

```
视频入库 → download → transcribe → subtitle_review
    → chapter_generate → interaction_generate → content_review
    → assemble → package → completed
```

题目解析视频跳过 `interaction_generate` 和 `content_review`。

## Key Decisions

- ASR 使用 `auto` 模式：先尝试 whisper.cpp，失败则回退 SenseVoice。
- 每个阶段失败都会将视频标记为 `failed`，支持从任意阶段重跑。
- Agent 阶段通过 OpenClaw 调用外部命令，模板化配置在 `config/pipeline.yaml` 中。

## Related Specs

- [批量从失败阶段重跑](../superpowers/completed/2026-06-02-batch-rerun-from-failed-phase-design.md)
- [Agent Legion Pipeline DAG](../superpowers/completed/2026-06-05-agent-legion-pipeline-dag-design.md)
