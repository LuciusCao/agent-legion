# Agent Legion 文档体系

本文档说明 Agent Legion 公开文档的结构与职责边界。

## 文档层级

| 层级 | 位置 | 受众 | 内容 |
|------|------|------|------|
| **现行架构** | `docs/architecture/`「现行文档」表 | 人 + Agent | 描述**当前系统状态**：模块划分、数据流、关键设计决策与契约 |
| **运维 runbook** | `docs/` 根下 | 运维 / 部署者 | 部署、Worker、材料存储、PostgreSQL、远程执行、外部集成的操作步骤 |
| **时点快照** | `docs/reviews/` | 人 | 系统性 / 性能质量 review 归档，按日期命名，证据反映审查时代码 |
| **历史设计记录** | `docs/architecture/`「历史设计记录」表 | 人 | 设计定稿、实施计划、PoC / 风险报告，仅供溯源 |

现行架构与运维 runbook 登记在文档漂移检查的白名单里，退役术语重现即被门禁拒绝（机制见
[architecture/docs-governance.md](architecture/docs-governance.md)）；时点快照与历史设计记录
不随代码更新，由文首 banner 标注后续演进。

> 开发过程中的设计规格（spec）与实施计划（plan）默认不随本仓库公开；历史上曾入库的
> `docs/plans/` 已连同提交历史一并移除。**例外**：对理解系统演进有长期参考价值的
> 设计定稿、实施计划与时点报告（PoC / 风险 review / 退役盘点）归档在
> `docs/architecture/` 的「历史设计记录」分区（见
> [architecture/README.md](architecture/README.md)），按时点快照管理——文中的
> `path:line` 证据反映当时代码，与现行语义冲突时以代码、现行文档与
> `config/architecture/architecture-invariants.yaml` 为准。

## 使用指南

- **想了解系统当前怎么工作的** → 看 `docs/architecture/`
- **部署与运维** → 看 `docs/` 下的部署文档与 runbook（Host/Worker 见
  [agent-worker-deployment.md](agent-worker-deployment.md)；材料存储
  SeaweedFS/S3 见 [materials-storage-deployment.md](materials-storage-deployment.md)；
  PostgreSQL 运维见 [postgresql-runbook.md](postgresql-runbook.md)；远程执行见
  [remote-execution-runbook.md](remote-execution-runbook.md)）
- **Studio**（编排、内置 agent 对话、MCP 集成）→ 先看 [architecture/README.md「Studio 文档导航」](architecture/README.md#studio-文档导航)；
  外部 agent 接入 MCP 工具面看 [studio-agent-mcp.md](studio-agent-mcp.md)
- **外部系统免登录提交条目（workspace API token）** → 看
  [workspace-api-tokens.md](workspace-api-tokens.md)
- **想了解 `data/` 运行时目录布局** → 看 [data-layout.md](data-layout.md)
- **发版 / 写 Release Notes 与 CHANGELOG** → 看 [release-notes.md](release-notes.md)（排版红线、正文模板、三条产品线差异）
- **时点 review 报告**（系统性/性能质量 review 的归档）→ 看
  `docs/reviews/`（按日期命名，`path:line` 证据反映审查时代码）

## 维护规则

- `docs/architecture/` 中的文档应随代码演进同步更新：现行文档描述当前系统状态，
  历史设计记录按各文首状态 banner 标注的演进修订（新增 `.md` 必须登记进
  [architecture/README.md](architecture/README.md) 的两张索引表之一）。
