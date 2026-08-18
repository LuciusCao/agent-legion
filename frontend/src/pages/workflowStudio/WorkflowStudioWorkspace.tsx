import { useState } from 'react'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import { WorkflowStudioSplitLayout } from './WorkflowStudioSplitLayout'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import { EMPTY_WORKFLOW_GUIDANCE } from './workflowStudioEmptyState'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'

/** 左右分栏入口：右半 Agent 对话默认展开、可收起；点节点时详情在 Agent
 * 展开时替换左半 DAG、收起时占右半（DAG 保留）。移动端退化为
 * 画布/编辑节点/Agent 三面板切换。 */
export function WorkflowStudioWorkspace(props: StudioLayoutProps) {
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    props.selectedNodeKey
  )
  const [agentOpen, setAgentOpen] = useState(true)

  return (
    <>
      {props.loadState === 'empty' && (
        <p role="status">{EMPTY_WORKFLOW_GUIDANCE}</p>
      )}
      <WorkflowStudioMobileNav
        value={mobilePanel}
        editorAvailable={props.selectedNodeKey !== null}
        onChange={setMobilePanel}
      />
      <WorkflowStudioSplitLayout
        props={props}
        mobilePanel={mobilePanel}
        agentOpen={agentOpen}
        onToggleAgent={() => setAgentOpen((open) => !open)}
      />
    </>
  )
}
