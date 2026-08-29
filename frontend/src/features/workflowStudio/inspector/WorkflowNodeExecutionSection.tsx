import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowAgentDefinitionCard } from './WorkflowAgentDefinitionCard'
import { WorkflowAgentExecutionDetails } from './WorkflowAgentExecutionDetails'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  workflowKey: string
  readOnly?: boolean
}
export function WorkflowNodeExecutionSection(props: Props) {
  const agent = props.agentCatalog.find(
    (definition) => definition.capability === props.node.capability
  )
  return (
    <section className={inspectorStyles.section} aria-label="节点执行能力">
      <div className={inspectorStyles.sectionTitle}>
        {agent ? 'Agent 配置' : '代码节点'}
      </div>
      {agent ? (
        <>
          <WorkflowAgentDefinitionCard definition={agent} />
          <WorkflowAgentExecutionDetails
            node={props.node}
            runtime={agent.runtime}
            definitionYaml={props.definitionYaml}
            setDefinitionYaml={props.setDefinitionYaml}
            readOnly={props.readOnly}
          />
        </>
      ) : (
        // P-0.5：无 Agent 路由的节点一律进入内置 code 池，无绑定可配。
        <div className={inspectorStyles.empty}>内置 code 池执行</div>
      )}
      <WorkflowNodeAgentEditor
        agentId={agent?.id ?? null}
        capability={props.node.capability}
        readOnly={props.readOnly}
      />
    </section>
  )
}
