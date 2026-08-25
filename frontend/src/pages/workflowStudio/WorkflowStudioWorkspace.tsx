import { useState } from 'react'
import { WorkflowCatalogLoadError } from './WorkflowCatalogLoadError'
import { WorkflowStudioEmptyGuide } from './WorkflowStudioEmptyGuide'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import { WorkflowStudioSplitLayout } from './WorkflowStudioSplitLayout'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import { useStudioState } from './studioStateContext'

/** 左右分栏入口：右半 Agent 对话默认展开、可收起；点节点时详情在 Agent
 * 展开时替换左半 DAG、收起时占右半（DAG 保留）。移动端退化为
 * 画布/编辑节点/Agent 三面板切换。 */
export function WorkflowStudioWorkspace() {
  const studio = useStudioState()
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    studio.selectedNodeKey
  )
  const [agentOpen, setAgentOpen] = useState(true)

  return (
    <>
      {studio.loadState === 'empty' && <WorkflowStudioEmptyGuide />}
      {studio.agentCatalogError && (
        <WorkflowCatalogLoadError onRetry={studio.retryAgentCatalog} />
      )}
      <WorkflowStudioMobileNav
        value={mobilePanel}
        editorAvailable={studio.selectedNodeKey !== null}
        onChange={setMobilePanel}
      />
      <WorkflowStudioSplitLayout
        mobilePanel={mobilePanel}
        agentOpen={agentOpen}
        onToggleAgent={() => setAgentOpen((open) => !open)}
      />
    </>
  )
}
