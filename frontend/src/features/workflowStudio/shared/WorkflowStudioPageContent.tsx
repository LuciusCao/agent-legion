import type { useWorkflowStudio } from './useWorkflowStudio'
import { nodeKeyForAgent } from './workflowStudioNav'
import { StudioNavContext, useStudioNavState } from './useStudioNavState'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'
import { useUiStore } from '../../../stores/uiStore'

type Studio = ReturnType<typeof useWorkflowStudio>

const NO_BINDING_NODE_TOAST =
  '当前 workflow 草稿中没有绑定该 Agent capability 的节点'

export function WorkflowStudioPageContent(props: { studio: Studio }) {
  const { workflow, agentCatalog, agentDefinitions, setSelectedNodeKey } =
    props.studio
  const showToast = useUiStore((s) => s.showToast)
  // Agent 管理弹窗已删除：nav 通道收敛为「选中绑定该 capability 的节点」，
  // agent 的查看/编辑在节点详情内嵌完成。#387：draft-only Agent 经
  // agentDefinitions 回落解析；capability 无节点绑定（空 workflow）时 toast
  // 反馈，不再静默空转。openAgent 同时记住目标草稿身份（codex P1）。
  const nav = useStudioNavState(
    (agentId) =>
      nodeKeyForAgent(agentId, workflow, agentCatalog, agentDefinitions),
    setSelectedNodeKey,
    () => showToast(NO_BINDING_NODE_TOAST, 'error')
  )
  // studio/view 状态由 PageHost 层的 StudioStateContext/StudioViewContext 提供，
  // 这里只补 nav 通道。
  return (
    <StudioNavContext.Provider value={nav}>
      <WorkflowStudioLayout />
    </StudioNavContext.Provider>
  )
}
