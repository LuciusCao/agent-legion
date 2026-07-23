# 视频流水线架构

## Overview

视频流水线将单个视频从 URL 处理为 CMS 可导入的 ZIP 包。流程作为 `video_knowledge` workflow 的 8 个节点执行，由后台 Workflow Worker 自动驱动。

## Directory Structure

```
server/app/pipeline/
├── download.py             # HTTP 视频下载
├── transcribe.py           # ASR 转录编排（whisper.cpp / SenseVoice）
├── transcribe_providers.py # ASR provider 实现
├── transcribe_sensevoice.py# SenseVoice 转写脚本
├── assemble.py             # metadata.json 组装
├── upload_params.py        # upload_params.json 生成
├── package.py              # 单视频 ZIP 打包
├── workspace_package.py    # Workspace 批量打包
├── runners.py              # OpenClaw runner 发现与构造
├── common.py               # 共享工具（SRT 解析、ID 生成等）
└── references/             # Agent 阶段参考文档
```

Agent Legion 流水线（与视频流水线独立）：

```
server/app/workflows/
├── definition.py           # DAG 定义加载
├── scheduler.py            # 下游节点解析
├── workflow_node_execution.py # 单节点执行
├── registry.py             # capability → handler 注册
├── loader.py               # workflow YAML 加载
├── validator.py            # workflow 定义校验
├── pi_runner.py            # Pi Agent 调用
├── skills.py               # Skill 路径解析 / 契约检查
├── video_knowledge.py      # 知识视频节点 handler
├── question_comprehension_info.py # 审题信息节点 handler
└── ...
```

## Data Flow

```
视频入库 → download → transcribe → subtitle_review
    → chapter_generate → interaction_generate → content_review
    → assemble → package → completed
```

## Key Decisions

- ASR 使用 `auto` 模式：先尝试 whisper.cpp，失败则回退 SenseVoice。
- 每个阶段失败都会将视频标记为 `failed`，支持从任意阶段重跑。
- Agent 阶段通过 OpenClaw 调用外部命令，模板化配置在 `config/video_hive.yaml` 中。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 视频流水线阶段

**知识视频（8 阶段）：**
`download` → `transcribe` → `subtitle_review` → `chapter_generate` → `interaction_generate` → `content_review` → `assemble` → `package`

<!-- END AUTO-GENERATED -->

## Related Specs

- [批量从失败阶段重跑](../superpowers/completed/2026-06-02-batch-rerun-from-failed-phase-design.md)
- [Agent Legion Pipeline DAG](../superpowers/completed/2026-06-05-agent-legion-pipeline-dag-design.md)
