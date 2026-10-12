import type { SwitchableNodeType } from '../shared/workflowStudioYamlDraft.nodeType'

// 可切换类型判定：start 是契约入口（每 DAG 恰一个），不进选择器。
export function isSwitchableNodeType(
  nodeType: string | undefined
): nodeType is SwitchableNodeType {
  return nodeType === 'code' || nodeType === 'agent' || nodeType === 'approval'
}

// 切到 approval 会剥掉的字段（确认文案用；与 nodeTypeSwitch 的
// APPROVAL_FORBIDDEN_FIELDS 镜像清单保持同步）。
export const APPROVAL_SWITCH_WARNING =
  '切换为审批门将清除该节点的 capability、execution、skill、' +
  'shard/reduce、config_schema 与审批白名单以外的 config，且不可撤销' +
  '（草稿历史可在 workflow-draft 版本中回退）。确定切换吗？'

// 破坏性清洗的确认（P1：设计稿 §4 要求确认文案明示清除范围；草稿自动
// 保存，误选即覆盖）。取消时不动草稿——select 是受控组件，React 会把
// 显示值弹回当前类型，无需手动恢复。由 useNodeTypeSwitch 在前置校验
// 通过后调用（校验都不过就没有问「确定清除吗」的意义）。
export function confirmDestructiveSwitch(targetType: SwitchableNodeType) {
  return targetType !== 'approval' || window.confirm(APPROVAL_SWITCH_WARNING)
}

// approval→code/agent 的原子补能力通道（#405）：结构化 UI 对 approval
// 隐藏能力 Key 输入（loader 禁令），「先在基本设置补 capability 再切」
// 在该形态不可达；切换时在弹窗里一次收齐，取消即放弃切换（草稿不动）。
// 语义：取消/空输入返回 null（调用侧放弃切换，保持原类型）；已有
// capability 的节点不经本弹窗（非 approval 源类型本就有该字段）。
export function promptForSwitchCapability(
  targetType: SwitchableNodeType
): string | null {
  const value = window.prompt(
    `切换为 ${targetType} 需要能力 Key（capability），请输入：`
  )
  return value === null || value.trim() === '' ? null : value.trim()
}
