import type {
  AgentDefinition,
  ExecutorDefinition,
} from '../../types/executorTypes'
import type { WorkflowNodeRecord } from '../../types'
import { WorkflowAgentDefinitionCard } from './WorkflowAgentDefinitionCard'
import { WorkflowAgentExecutionDetails } from './WorkflowAgentExecutionDetails'
import { WorkflowNodeBindingEditor } from './WorkflowNodeBindingEditor'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  executorCatalog: ExecutorDefinition[]
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  workflowKey: string
  readOnly?: boolean
}
export function WorkflowNodeExecutionSection(props: Props) {
  const bindings = findCapabilityBindings(
    props.executorCatalog,
    props.node.capability
  )
  const agent = props.agentCatalog.find(
    (definition) => definition.capability === props.node.capability
  )
  return (
    <section className={inspectorStyles.section} aria-label="节点执行能力">
      <div className={inspectorStyles.sectionTitle}>
        {agent ? 'Agent 配置' : '本地执行'}
      </div>
      {agent ? (
        <WorkflowAgentDefinitionCard definition={agent} />
      ) : (
        <WorkflowNodeBindingEditor
          node={props.node}
          bindings={bindings}
          executorCatalog={props.executorCatalog}
          workflowKey={props.workflowKey}
          readOnly={props.readOnly}
        />
      )}
      {agent && (
        <WorkflowAgentExecutionDetails
          definition={agent}
          node={props.node}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
      )}
    </section>
  )
}
