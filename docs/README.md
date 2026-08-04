# Agent Legion 文档体系

本文档说明 Agent Legion 公开文档的结构与职责边界。

## 文档层级

| 层级 | 位置 | 受众 | 内容 |
|------|------|------|------|
| **架构文档** | `docs/architecture/` | 人 + Agent | 描述**当前系统状态**：模块划分、数据流、关键设计决策 |

> 开发过程中的设计规格（spec）与实施计划（plan）属于内部设计文档，不随本仓库公开；
> 历史上曾入库的 `docs/plans/` 已连同提交历史一并移除。

## 使用指南

- **想了解系统当前怎么工作的** → 看 `docs/architecture/`
- **部署与运维** → 看 `docs/` 下的部署文档与 runbook
- **想了解 `data/` 运行时目录布局** → 看 [data-layout.md](data-layout.md)

## 维护规则

- `docs/architecture/` 中的文档应随代码演进同步更新。
