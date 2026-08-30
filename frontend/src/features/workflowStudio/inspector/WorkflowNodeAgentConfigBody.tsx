import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowAgentDefinitionCard } from './WorkflowAgentDefinitionCard'
import { WorkflowAgentExecutionDetails } from './WorkflowAgentExecutionDetails'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  agentDefinition: AgentDefinition | undefined
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// type=agent 节点的执行能力主体：有 published Agent 时给配置卡 + 执行细节；
// 缺失时给指引（发布门禁要求恰好一个 published Agent，会显式报错）。
export function WorkflowNodeAgentConfigBody(props: Props) {
  if (!props.agentDefinition) {
    return (
      <div className={inspectorStyles.empty}>
        该 capability 暂无 published Agent；发布 workflow 前需新建并发布一个。
      </div>
    )
  }
  return (
    <>
      <WorkflowAgentDefinitionCard definition={props.agentDefinition} />
      <WorkflowAgentExecutionDetails
        node={props.node}
        runtime={props.agentDefinition.runtime}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
    </>
  )
}
