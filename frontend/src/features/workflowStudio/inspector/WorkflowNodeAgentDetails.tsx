import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowAgentDefinitionCard } from './WorkflowAgentDefinitionCard'
import { WorkflowAgentExecutionDetails } from './WorkflowAgentExecutionDetails'
import { WorkflowNodeSkillEditor } from './WorkflowNodeSkillEditor'

type Props = {
  node: WorkflowNodeRecord
  definition: AgentDefinition
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

/** Agent 路由节点的定义卡 + skill 绑定（#76）+ 执行详情；
 * 自 WorkflowNodeExecutionSection 拆出以守行数预算。 */
export function WorkflowNodeAgentDetails(props: Props) {
  return (
    <>
      <WorkflowAgentDefinitionCard definition={props.definition} />
      <WorkflowNodeSkillEditor
        node={props.node}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowAgentExecutionDetails
        node={props.node}
        runtime={props.definition.runtime}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    </>
  )
}
