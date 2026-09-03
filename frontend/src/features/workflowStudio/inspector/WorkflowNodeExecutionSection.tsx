import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeAgentConfigBody } from './WorkflowNodeAgentConfigBody'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'
import type { AgentBindingStatus } from './useAgentCatalog'
import { useCapabilityAgent } from './useAgentDefinitions'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  /** capability→Agent 绑定的目录 settle 状态（useAgentCatalog，#426 review P2）。 */
  agentBindingStatus: AgentBindingStatus
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}
// #392 Phase 2：类型注册表只把本 section 挂在 code / agent 类型上，
// approval 由专属的 WorkflowNodeApprovalConfigSection 承载，节点内不
// 再需要类型分叉。
export function WorkflowNodeExecutionSection(props: Props) {
  const { node } = props
  // #387：draft-only Agent 回落 agent-definitions（useCapabilityAgent；
  // 路由 workspaceId 与 openAgent 的 pendingAgentId 也在该 hook 内消费）。
  const { agent, isDraft } = useCapabilityAgent(props)
  // 防御：#392 Phase 2 起注册表只把本 section 挂在 code/agent 类型上，
  // approval 由 WorkflowNodeApprovalConfigSection 承载。直接喂 approval
  // 时渲染空（不落入误导性的「代码节点」文案）。hooks 在早退前调用。
  if (node.node_type === 'approval') return null
  // #284/#392：节点类型由显式 node_type 判定；Agent 解析经 useCapabilityAgent
  // （published 目录优先，draft-only 回落）。Agent 编辑入口只属于 agent
  // 节点——code 节点的类型变更走头部类型选择器，不再在此处长出 Agent 入口。
  const isAgentNode = node.node_type === 'agent'
  return (
    <section className={inspectorStyles.section} aria-label="节点执行能力">
      <div className={inspectorStyles.sectionTitle}>
        {isAgentNode ? 'Agent 配置' : '代码节点'}
      </div>
      {isAgentNode ? (
        <>
          <WorkflowNodeAgentConfigBody
            node={node}
            agentDefinition={agent}
            isDraft={isDraft}
            definitionYaml={props.definitionYaml}
            setDefinitionYaml={props.setDefinitionYaml}
            readOnly={props.readOnly}
          />
          <WorkflowNodeAgentEditor
            agentId={agent?.id ?? null}
            capability={node.capability}
            bindingStatus={props.agentBindingStatus}
            readOnly={props.readOnly}
          />
        </>
      ) : (
        // type=code：内置 code 池执行，无绑定可配。
        <div className={inspectorStyles.empty}>内置 code 池执行</div>
      )}
    </section>
  )
}
