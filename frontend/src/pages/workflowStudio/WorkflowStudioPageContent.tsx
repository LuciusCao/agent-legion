import { useMemo } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import { nodeKeyForAgent, StudioNavContext } from './workflowStudioNav'
import type { StudioNav } from './workflowStudioNav'
import { WorkflowStudioLayout } from './WorkflowStudioLayout'

type Studio = ReturnType<typeof useWorkflowStudio>

export function WorkflowStudioPageContent(props: { studio: Studio }) {
  const { studio } = props
  // Agent 管理弹窗已删除：nav 通道收敛为「选中绑定该 capability 的节点」，
  // agent 的查看/编辑在节点详情内嵌完成。useMemo 稳住 context value。
  const { workflow, agentCatalog } = studio
  const nav: StudioNav = useMemo(
    () => ({
      openAgent: (agentId) => {
        const key = nodeKeyForAgent(agentId, workflow, agentCatalog)
        if (key) studio.setSelectedNodeKey(key)
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只依赖 nav 实际消费的稳定字段
    [workflow, agentCatalog, studio.setSelectedNodeKey]
  )
  // studio/view 状态由 PageHost 层的 StudioStateContext/StudioViewContext 提供，
  // 这里只补 nav 通道。
  return (
    <StudioNavContext.Provider value={nav}>
      <WorkflowStudioLayout />
    </StudioNavContext.Provider>
  )
}
