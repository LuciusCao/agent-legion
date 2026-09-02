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
