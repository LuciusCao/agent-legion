# Video Hive 文档体系

本文档说明 Video Hive 的三层文档体系及其职责边界。

## 三层文档

| 层级 | 位置 | 受众 | 内容 |
|------|------|------|------|
| **架构文档** | `docs/architecture/` | 人 + Agent | 描述**当前系统状态**：模块划分、数据流、关键设计决策 |
| **设计规格** | `docs/superpowers/specs/` | Agent（开发时） | 描述**开发过程中的设计决策**：为何这样设计、考虑过哪些方案 |
| **实施计划** | `docs/superpowers/plans/` | Agent（执行时） | 描述**如何编码实现**：文件路径、代码、测试、命令 |

## 使用指南

- **想了解系统当前怎么工作的** → 看 `docs/architecture/`
- **想知道某个功能当初为什么这样设计** → 看 `docs/superpowers/completed/` 或 `docs/superpowers/archive/`
- **正在开发新功能，需要执行步骤** → 看 `docs/superpowers/plans/`
- **想确认 spec 是否还反映当前代码** → 看 `docs/superpowers/SPEC_HEALTH.md`

## 维护规则

- `docs/architecture/` 中的文档应随代码演进同步更新（阶段 3：按需迁移）。
- `docs/superpowers/specs/` 中的进行中标规格在开发完成后自动移入 `completed/` 或 `archive/`。
- `docs/superpowers/SPEC_HEALTH.md` 由 `scripts/verify_specs.py` 自动生成，勿手动编辑。
