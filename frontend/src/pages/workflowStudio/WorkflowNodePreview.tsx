import type { WorkflowNodeRecord } from '../../types'
import type { AgentDefinition } from '../../types/agentCatalogTypes'
import type { NodeDetailPreviewKind } from './nodeDetailPreviewContext'
import { buildWorkflowNodePromptPreview } from './workflowNodePromptPreview'
import { parseWorkflowNode } from './workflowStudioYamlDraft.parse'
import { WorkflowPromptPreviewPanel } from './WorkflowPromptPreviewPanel'
import { WorkflowSkillPreviewPanel } from './WorkflowSkillPreviewPanel'

type Props = {
  kind: NodeDetailPreviewKind
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  onBack: () => void
}

/** 详情 panel 预览视图分发：prompt 复用前端拼装（追加指令草稿 YAML 优先，
 * 未保存的编辑即时反映；解析失败回落基线节点），skill 按 capability 绑定的
 * Agent 技能拉取文件列表渲染。 */
export function WorkflowNodePreview(props: Props) {
  const agent = props.agentCatalog.find(
    (definition) => definition.capability === props.node.capability
  )
  const skillKey = agent?.skill ?? ''
  if (props.kind === 'prompt') {
    const draft = parseWorkflowNode(props.definitionYaml, props.node.key)
    const additionalPrompt = draft
      ? (draft.execution?.prompt ?? '')
      : (props.node.execution?.prompt ?? '')
    return (
      <WorkflowPromptPreviewPanel
        nodeLabel={props.node.label}
        prompt={buildWorkflowNodePromptPreview(
          props.node,
          skillKey,
          additionalPrompt
        )}
        onBack={props.onBack}
      />
    )
  }
  return <WorkflowSkillPreviewPanel skillKey={skillKey} onBack={props.onBack} />
}
