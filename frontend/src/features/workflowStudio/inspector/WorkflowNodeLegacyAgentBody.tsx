import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import type { AgentBindingStatus } from './agentBindingStatus'
import { WorkflowNodeAgentConfigBody } from './WorkflowNodeAgentConfigBody'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'
import { WorkflowNodeRuntimeSelect } from './WorkflowNodeRuntimeSelect'
import inspectorStyles from './WorkflowNodeInspector.module.css'

/**
 * #935（#440 P3）：v93 未能内联的 legacy agent 节点（迁移报告里的未解析
 * 节点）。发布门禁要求先补 `execution.runtime`——提示 + runtime 下拉放在
 * 最前；旧 Agent 编辑入口暂留作参考（P3b 删除）。
 */
export function WorkflowNodeLegacyAgentBody(props: {
  node: WorkflowNodeRecord
  agentDefinition: AgentDefinition | undefined
  isDraft: boolean
  bindingStatus: AgentBindingStatus
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  return (
    <>
      <div className={inspectorStyles.empty}>
        该节点尚未内联执行档案，发布前需补 execution.runtime（在下方选择
        runtime，并在节点上声明 skill / tools）。
      </div>
      <WorkflowNodeRuntimeSelect
        node={props.node}
        nodeRuntime=""
        defaultRuntime=""
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowNodeAgentConfigBody
        node={props.node}
        agentDefinition={props.agentDefinition}
        isDraft={props.isDraft}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        readOnly={props.readOnly}
      />
      <WorkflowNodeAgentEditor
        agentId={props.agentDefinition?.id ?? null}
        capability={props.node.capability}
        bindingStatus={props.bindingStatus}
        readOnly={props.readOnly}
      />
    </>
  )
}
