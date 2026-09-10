import { useCallback } from 'react'
import { useUiStore } from '../../../stores/uiStore'
import {
  patchWorkflowNodeType,
  WorkflowNodeTypeSwitchError,
  type SwitchableNodeType,
} from '../shared/workflowStudioYamlDraft.nodeType'
import { confirmDestructiveSwitch, promptForSwitchCapability } from './nodeTypeSelector'

// 头部类型选择器（#392）的写侧接线：先做目标类型前置校验
// （capability/入边，校验都不过就不必问破坏性确认），确认通过后改写
// 草稿 YAML 并按目标类型清洗字段。校验失败 toast 提示并保留原类型；
// 不可恢复错误降级提示手动改 YAML。
// #405：approval 按契约无 capability、结构化 UI 又隐藏该输入，切回
// code/agent 的 capability 缺失必须经弹窗随本次切换原子补上（一次
// 提交，无中间非法态）；已有 capability 的节点维持原直通路径。
export function useNodeTypeSwitch(
  definitionYaml: string,
  nodeKey: string,
  nodeType: string | undefined,
  setDefinitionYaml: (value: string) => void
) {
  const showToast = useUiStore((s) => s.showToast)
  return useCallback(
    (nodeTypeTarget: SwitchableNodeType) => {
      // approval 切出（无 capability）先弹补能力通道，取消/留空即放弃
      // 本次切换，草稿保持原类型；其余类型直通（prompt 非空才继续）。
      const promptResult =
        nodeTypeTarget !== 'approval' && nodeType === 'approval'
          ? promptForSwitchCapability(nodeTypeTarget)
          : undefined
      if (promptResult === null) return false
      try {
        const nextYaml = patchWorkflowNodeType(
          definitionYaml,
          nodeKey,
          nodeTypeTarget,
          promptResult ?? undefined
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
