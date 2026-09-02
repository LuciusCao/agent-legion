# Studio 节点类型抽象落地：类型选择器 + 按类型注册设置区块

状态：**设计稿（待评审）**。承接 issue #392；worktree
`feat/studio-node-type-selector-392`。本文档给出问题分析、目标形态、
分阶段实施方案与取舍记录，`path:line` 证据以 develop@e4e4590c 为准。

上游语境：#284 引入显式 `type: code|agent`（invariant
`EXEC-WORKFLOW-NODE-TYPE-001`）；#266 加入 `type: approval`（人工审批门，
`EXEC-APPROVAL-001`）；#387 修补了 draft-only Agent 的 Studio 发布闭环。
本设计是这三条线的 UI 侧收口。

## 1. 问题

后端节点类型抽象已完备：`WorkflowNode.node_type` 取值
`start | code | agent | approval`（`server/app/workflows/schema.py:98`），
loader 按类型做字段级禁令，发布门禁按类型 fail-closed 校验
（`config/architecture/architecture-invariants.yaml:1239`）。但 Studio 前端
停留在 #284 之前的过渡形态，具体缺口：

1. **无类型选择器**。节点类型的唯一 UI 变更入口是埋在「节点执行能力」区块
   内的「切换为 Agent 执行」按钮，且仅 code→agent 单向
   （`frontend/src/features/workflowStudio/shared/workflowStudioYamlDraft.nodeType.ts:15`
   的 `patchWorkflowNodeType` 签名只收 `'code' | 'agent'`）。agent→code、
   →approval 只能手改 YAML。code 节点上长出 Agent 编辑入口，正是用户
   困惑的来源（issue #392 起因）。
2. **类型分支散落在各 section 内部**。
   `WorkflowNodeInspectorSections.tsx:30` 只对 start 做整体替换，其余类型
   「所有 section 全渲染、每个 section 内部自行按 node_type 分叉」：
   ExecutionSection（`WorkflowNodeExecutionSection.tsx:22-26`）、
   ConfigSchemaSection（agent 生效 schema / code 声明 schema）、
   CodeSection（`isCodeNode` 门控）。「每个类型有不同的设置和能力」在
   行为上存在、在结构上不存在。
3. **approval 节点无 UI 创建路径**。全局检索确认 approval 在前端只出现在
   读取路径（ghost 解析、执行警告、审批区块展示）；DAG 徽标
   （`workflowStudioDagBadges.ts:13`）只特判 start。想用审批门只能手写
   YAML，与「DAG 内人工决策关卡」的定位不符。
4. **无节点创建入口**。新节点（含审批门）目前唯一路径是「编辑 YAML」
   Dialog 手写（空态模板除外），画布/inspector 均无添加动作。

## 2. 目标形态

**节点详情顶部一个类型选择器（type selector），inspector 主体按类型注册
section 集，切换类型时按目标类型清洗 YAML 字段。**

```
┌──────────────────────────────────────────────┐
│ 节点标签                          [关闭]      │
│ 类型: [code ▾]   ← 选择器（start 不可切）     │
│ node-key                                     │
├──────────────────────────────────────────────┤
│ <按类型注册的 section 集>                     │
│  code:     基本设置/节点代码/声明 schema/     │
│            配置/数据契约/依赖                 │
│  agent:    基本设置/Agent 配置/skill 绑定/    │
│            运行时设置/数据契约/依赖           │
│  approval: 基本设置/审批配置/数据契约/依赖    │
│  start:    入口契约（现状保持）               │
└──────────────────────────────────────────────┘
```

原则：

- **类型是一等概念**：类型决定「这个节点是什么、能配什么」，而不是散落
  的 if 分支。新增类型 = 注册表加一行 + 字段清洗规则 + 徽标，不再触碰
  每个已有 section。
- **YAML 草稿仍是唯一事实源**：所有变更继续走 `patchNode` →
  parse/dump → `setDefinitionYaml`（现有链路），不引入平行状态。类型
  切换只是「改 `type` + 清洗字段」的复合 patch。
- **loader 禁令前端镜像**：切换到 approval 时必须剥掉
  `execution/skill/shard/reduce/config_schema/capability`，否则下一次
  validate/publish 就被 loader 拒绝（
  `server/app/workflows/approval_node.py:38` `_FORBIDDEN_APPROVAL_FIELDS`）。
  清洗规则以后端禁令为唯一蓝本，禁令变更时同步镜像。
- **现有 Agent 闭环逻辑保留、入口归位**：「建 Agent 草稿 → 发布 →
  type 切换生效」的面板内闭环（#387）不丢，但触发方式改为：切到
  agent 且 capability 无 published Agent 时内联引导。

## 3. 实施方案（三个 phase，可独立合入）

### Phase 1：类型选择器 + patch 通用化（核心，先行）

**YAML 层** — 改造
`frontend/src/features/workflowStudio/shared/workflowStudioYamlDraft.nodeType.ts`：

```
patchWorkflowNodeType(rawYaml, nodeKey, targetType: NodeType): string
```

- 类型联合对齐后端取值域 `code | agent | start | approval`（读侧容忍
  遗留 `node`，`WorkflowYamlNode.type` 注释已有此约定）。
- start 不可切出（现有行为）；**切入 start 一并禁止**——一个 DAG 只允许
  一个 start（`ensure_start_node` 会拒绝第二个），选择器根本不提供该选项，
  保持 fail-closed。
- 切换时按目标类型做**字段清洗**，规则镜像后端：

  | 目标类型 | 清洗动作 |
  |---|---|
  | code | 剥 `skill`（#76：code 节点禁 skill）；剥 approval 专属 config 键（`rework_target/feedback_artifact`） |
  | agent | 保留 `skill`；剥 approval 专属 config 键；保留 `capability`（门禁要求） |
  | approval | 剥 `capability/execution/skill/shard/reduce/config_schema`；config 只留 `rework_target/feedback_artifact`（白名单） |

  清洗放独立纯函数 `sanitizeNodeForType(node, targetType)`，便于单测
  直接对拍后端 `validate_approval_fields` 的用例。

**Inspector 头部** — `WorkflowNodeInspectorHeader.tsx`：kind 徽标位置
替换为只读 MUI `Select`（readOnly 时退化为现有徽标）。选项
`code/agent/approval`，start 不出现。onChange → `patchWorkflowNodeType`
→ `setDefinitionYaml`，失败 toast 降级提示（沿用 switchToAgent 的降级
文案模式）。

**Agent 入口归位** — `WorkflowNodeExecutionSection.tsx`：code 类型不再
渲染 `WorkflowNodeAgentEditor`。该入口移入 agent 类型的 section 集
（Phase 2 注册表里 agent 的条目）；capability 已有 Agent 的「编辑
Agent」便利入口保留在 agent 节点上。#387 的面板内闭环逻辑
（`WorkflowNodeAgentEditorPanel.handleSaved`）原样迁移，仅触发条件从
「code 节点点切换按钮」变为「切到 agent 后 capability 无 published
Agent 的内联引导」。

### Phase 2：按类型注册 section 集（结构重构）

新文件
`frontend/src/features/workflowStudio/inspector/nodeTypeRegistry.ts`：

```ts
type NodeTypeSectionSpec = {
  sections: ComponentType<InspectorSectionProps>[]   // 该类型渲染的 section 序列
}
const REGISTRY: Record<'code'|'agent'|'approval', NodeTypeSectionSpec>
```

- section 组件全部复用现有组件（Editor/Execution/Code/ConfigSchema/
  Config/DataContract/Dependency/Start），只是组合关系从「渲染全部 +
  内部分叉」改为「按类型挑序列」。各 section 内部的 node_type 分叉
  （如 ExecutionSection 的 approval 早退、`isCodeNode` 门控）随之删除
  ——类型已由注册表保证。
- `WorkflowNodeInspectorSections.tsx` 变为查表分发，start 分支保留。
- 审批配置升级：approval 类型给专属 section（`rework_target` 下拉
  [上游节点] + `feedback_artifact` 输入），替换现在的只读说明块
  （`WorkflowNodeAgentConfigBody.tsx:49` `WorkflowNodeApprovalSection`）。
  写路径走 `patchNode` 系（config 白名单键）。

### Phase 3：approval 的画布可见性 + 节点创建入口（补齐体验）

- **DAG 徽标**：`workflowStudioDagBadges.ts` 为 approval 加专属徽标
  （拓扑徽标 entry/branch/terminal 照常叠加），与 start 徽标同款样式。
- **节点创建**：画布工具栏（`WorkflowStudioCanvasToolbar.tsx`）加
  「添加节点」：选类型（code/agent/approval）→ key/label/capability
  （approval 不需要 capability）→ 追加进草稿 YAML（新 patch helper
  `appendWorkflowNode`）→ 选中新节点打开 inspector。边的接线仍走
  YAML 编辑（本 phase 不做拖拽连线，避免与 XYFlow 交互改造耦合）。
- **执行警告**：`workflowStudioExecutionWarnings.ts` 确认 approval
  节点不产生「execution 缺口」误报（现状注释已排除，加回归用例钉住）。

### 不做 / 明确排除

- **不做类型自由扩展的插件机制**：注册表是代码级常量，非运行时插件。
  未来新类型（http/sub-workflow/…）走「注册表加一行 + 后端 loader 取值
  域扩展 + schema 迁移」的正常演进，不在本期预研。
- **不做 agent→code 之外的批量转换**、不做 DAG 拖拽编辑。
- **不改后端**：本设计是纯前端收口；后端 `node_type` 抽象与门禁已就位。
  唯一后端接触点是 validate API 已有的报错信息，作为前端清洗规则的
  验收对照。

## 4. 兼容与迁移

- **草稿兼容**：类型切换只作用于草稿 YAML；已发布 revision 不受影响
  （发布才产生新 revision）。遗留 `type: node` / 缺省 type 的草稿在
  parse 层已归一化为 code（ghost 与 record 双路径一致，
  `workflowStudioGhostNode.ts:47`、`workflowYamlDraftRecord.ts:20`），
  选择器显示 `code`，无需迁移。
- **字段清洗的取舍**：code→approval→code 往返会永久丢失
  execution/skill 配置。这是 fail-closed 的代价（保留字段 = 下一次
  validate 必炸）。UI 上在切换确认文案中明示「将清除 X 类字段」，
  不做暂存回填——草稿有版本化的 workflow-draft API 兜底可回退。
- **测试基线**：清洗规则纯函数直接对拍
  `tests/workflows/test_approval_node_definition.py` 的字段禁令用例；
  UI 层沿用 `WorkflowNodeInspector.test.tsx` 的渲染断言模式，新增
  「code 节点无 Agent 入口」「approval 节点无 execution section」等
  负向断言（正是本次用户反馈的回归线）。

## 5. 验收标准

1. code 节点 inspector 不再出现任何 Agent 创建/编辑入口；类型选择器在
   头部，code/agent/approval 三选一。
2. 任一类型切换后，草稿 YAML 立即通过 `validateWorkflowDraft`
   （不因类型禁令字段报错）。
3. approval 节点经 UI 创建（Phase 3 后），带徽标、可配
   rework_target/feedback_artifact、无 execution/code/agent section。
4. 现有 e2e 关键路径不回归：draft-only Agent 闭环（#387）、start 契约
   编辑、code 节点代码编辑、publish 流程。

## 6. 风险与开放问题

- **R1 徽标→选择器的视觉回归**：头部徽标被 MUI Select 替换，紧凑布局
  （`WorkflowNodeInspectorHeader.module.css`）需适配移动端。低风险，
  样式微调。
- **R2 清洗规则与后端禁令漂移**：镜像规则可能随后端演进而过期。缓解：
  注释互相锚定（前端函数注释指向 `_FORBIDDEN_APPROVAL_FIELDS`），测试
  对拍后端用例；长期可由后端 export 禁令表生成（本期不做）。
- **O1 创建节点时 approval 的 edges 校验**：`validate_approval_edges`
  要求 approval 至少一条来自可执行节点的入边。新建未接线的 approval
  节点在 validate 时会报错——这是预期行为（引导用户接线），但创建
  流程的提示文案需说明，不静默吞错。
