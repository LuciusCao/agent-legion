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
 * 「应用到编辑器」把 agent 草稿灌回阶段 0/1 的草稿编辑器（先回 draft 视图）。 */
export function StudioRightPanelBody(props: WorkflowStudioRightPanelProps) {
  if (props.activeTab === 'chat') {
    return (
      <StudioChatPanel
        onApplyWorkflowDraft={(yaml) => {
          props.onBackToDraft?.()
          props.setDefinitionYaml(yaml)
        }}
        onSelectNode={props.onSelectNode}
      />
    )
  }
  return (
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
  )
}
