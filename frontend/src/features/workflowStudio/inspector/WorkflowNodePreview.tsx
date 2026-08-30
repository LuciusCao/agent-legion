import type { WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { NodeDetailPreviewKind } from './nodeDetailPreviewContext'
import { WorkflowPromptPreviewPanel } from './WorkflowPromptPreviewPanel'
import { WorkflowSkillPreviewPanel } from './WorkflowSkillPreviewPanel'

type Props = {
  kind: NodeDetailPreviewKind
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly: boolean
}

/** 详情 panel 预览视图分发：prompt 走后端预览 API 的编辑型面板（草稿 YAML
 * 实时参与预览，编辑回写草稿），skill 按 capability 绑定的 Agent 技能拉取
 * 文件列表渲染。预览入口只存在于 type=agent 节点的 Agent 配置卡；skill 查找
 * 同样以显式 node_type 为准（#284），code 节点即使 capability 命中 Agent
 * 目录也不展示其技能。 */
export function WorkflowNodePreview(props: Props) {
  const agent =
    props.node.node_type === 'agent'
      ? props.agentCatalog.find(
          (definition) => definition.capability === props.node.capability
        )
      : undefined
  const skillKey = agent?.skill ?? ''
  if (props.kind === 'prompt') {
    return (
      <WorkflowPromptPreviewPanel
        node={props.node}
        fallbackSkillKey={skillKey}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    )
  }
  return <WorkflowSkillPreviewPanel skillKey={skillKey} />
}
