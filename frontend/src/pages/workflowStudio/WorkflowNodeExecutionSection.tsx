import type { ExecutorDefinition } from '../../executorTypes'
import type { WorkflowNodeRecord } from '../../types'
import { WorkflowAgentExecutionDetails } from './WorkflowAgentExecutionDetails'
import {
  findCapabilityBindings,
  WorkflowExecutorBindingList,
} from './WorkflowExecutorBindingList'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  executorCatalog: ExecutorDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

export function WorkflowNodeExecutionSection(props: Props) {
  const bindings = findCapabilityBindings(
    props.executorCatalog,
    props.node.capability
  )
  const agentBinding = bindings.find(({ executor }) => executor.kind === 'pi')
  return (
    <section className={inspectorStyles.section} aria-label="节点执行能力">
      <div className={inspectorStyles.sectionTitle}>
        {agentBinding ? 'Agent 配置' : '本地执行'}
      </div>
      {bindings.length === 0 ? (
        <div className={inspectorStyles.empty}>
          未匹配到 executor capability
        </div>
      ) : (
        <WorkflowExecutorBindingList bindings={bindings} />
      )}
      {agentBinding && (
        <WorkflowAgentExecutionDetails
          binding={agentBinding}
          node={props.node}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
      )}
    </section>
  )
}
