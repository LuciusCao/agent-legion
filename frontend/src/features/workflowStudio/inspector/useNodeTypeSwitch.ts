import { useCallback } from 'react'
import { useUiStore } from '../../../stores/uiStore'
import {
  patchWorkflowNodeType,
  WorkflowNodeTypeSwitchError,
  type SwitchableNodeType,
} from '../shared/workflowStudioYamlDraft.nodeType'
import { confirmDestructiveSwitch } from './nodeTypeSelector'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'

// 头部类型选择器（#392）的写侧接线：先做目标类型前置校验
// （入边，校验不过就不必问破坏性确认），确认通过后改写草稿 YAML 并按
// 目标类型清洗字段。校验失败 toast 提示并保留原类型；不可恢复错误降级
// 提示手动改 YAML。code/agent 都允许空 capability；approval 切出时不再
// 弹窗补值，缺省由 patch 归一为 ''，草稿里的旧非空兼容标签继续保留。
export function useNodeTypeSwitch(
  definitionYaml: string,
  nodeKey: string,
  nodeType: string | undefined,
  setDefinitionYaml: (value: string) => void
) {
  const showToast = useUiStore((s) => s.showToast)
  return useCallback(
    (nodeTypeTarget: SwitchableNodeType) => {
      const capabilityChannel =
        nodeTypeTarget === 'code' && nodeType === 'approval'
          ? parseWorkflowNode(definitionYaml, nodeKey)?.capability
          : undefined
      try {
        const nextYaml = patchWorkflowNodeType(
          definitionYaml,
          nodeKey,
          nodeTypeTarget,
          capabilityChannel
        )
        if (!confirmDestructiveSwitch(nodeTypeTarget)) return false
        setDefinitionYaml(nextYaml)
        return true
      } catch (error) {
        if (error instanceof WorkflowNodeTypeSwitchError) {
          showToast(error.message, 'error')
        } else {
          showToast(
            `类型切换失败；请手动在 YAML 将节点 type 改为 ${nodeTypeTarget}`,
            'error'
          )
        }
        return false
      }
    },
    [definitionYaml, nodeKey, nodeType, setDefinitionYaml, showToast]
  )
}
