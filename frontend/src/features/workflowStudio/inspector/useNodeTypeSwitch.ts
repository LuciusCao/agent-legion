import { useCallback } from 'react'
import { useUiStore } from '../../../stores/uiStore'
import {
  patchWorkflowNodeType,
  WorkflowNodeTypeSwitchError,
  type SwitchableNodeType,
} from '../shared/workflowStudioYamlDraft.nodeType'
import { confirmDestructiveSwitch } from './nodeTypeSelector'

// 头部类型选择器（#392）的写侧接线：先做目标类型前置校验
// （capability/入边，校验都不过就不必问破坏性确认），确认通过后改写
// 草稿 YAML 并按目标类型清洗字段。校验失败 toast 提示并保留原类型；
// 不可恢复错误降级提示手动改 YAML。
export function useNodeTypeSwitch(
  definitionYaml: string,
  nodeKey: string,
  setDefinitionYaml: (value: string) => void
) {
  const showToast = useUiStore((s) => s.showToast)
  return useCallback(
    (nodeType: SwitchableNodeType) => {
      try {
        const nextYaml = patchWorkflowNodeType(
          definitionYaml,
          nodeKey,
          nodeType
        )
        if (!confirmDestructiveSwitch(nodeType)) return false
        setDefinitionYaml(nextYaml)
        return true
      } catch (error) {
        if (error instanceof WorkflowNodeTypeSwitchError) {
          showToast(error.message, 'error')
        } else {
          showToast(
            `类型切换失败；请手动在 YAML 将节点 type 改为 ${nodeType}`,
            'error'
          )
        }
        return false
      }
    },
    [definitionYaml, nodeKey, setDefinitionYaml, showToast]
  )
}
