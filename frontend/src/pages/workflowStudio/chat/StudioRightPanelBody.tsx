import { useState } from 'react'
import type {
  AgentDefinition,
  ExecutorDefinition,
} from '../../../types/executorTypes'
import type { WorkflowDefinitionRecord } from '../../../types'
import { WorkflowNodeInspector } from '../WorkflowNodeInspector'
import { StudioChatPanel } from './StudioChatPanel'
import type { StudioRightPanelTab } from './StudioRightPanelTabs'

export type WorkflowStudioRightPanelProps = {
  workflow: WorkflowDefinitionRecord | null
  executorCatalog: ExecutorDefinition[]
  agentCatalog: AgentDefinition[]
  selectedNodeKey: string | null
  readOnly: boolean
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  activeTab: StudioRightPanelTab
  onTabChange: (tab: StudioRightPanelTab) => void
  onBackToDraft?: () => void
  onSelectNode?: (nodeKey: string) => void
  onClose: () => void
}

/** 右栏 tab 主体：节点配置 Inspector 或 Agent 助手对话面板。
 * 「应用到编辑器」把 agent 草稿灌回阶段 0/1 的草稿编辑器（先回 draft 视图）。
 * chat 面板首次激活后保活（hidden 而非 unmount），tab 切换不丢会话选择与
 * 滚动状态；inspector 无本地状态，随 tab 重建。 */
export function StudioRightPanelBody(props: WorkflowStudioRightPanelProps) {
  // 首次切到 chat 才挂载，避免常驻 inspector 时白拉 agent/会话列表。
  // 渲染期派生 state（React 认可的 adjust-state-during-render 模式）。
  const [chatVisited, setChatVisited] = useState(props.activeTab === 'chat')
  if (props.activeTab === 'chat' && !chatVisited) {
    setChatVisited(true)
  }
  return (
    <>
      {chatVisited && (
        <div style={{ height: '100%' }} hidden={props.activeTab !== 'chat'}>
          <StudioChatPanel
            onApplyWorkflowDraft={(yaml) => {
              props.onBackToDraft?.()
              props.setDefinitionYaml(yaml)
            }}
            onSelectNode={props.onSelectNode}
            selectedNodeKey={props.selectedNodeKey}
          />
        </div>
      )}
      {props.activeTab !== 'chat' && (
        <WorkflowNodeInspector
          workflow={props.workflow}
          executorCatalog={props.executorCatalog}
          agentCatalog={props.agentCatalog}
          selectedNodeKey={props.selectedNodeKey}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
          onClose={props.onClose}
        />
      )}
    </>
  )
}
