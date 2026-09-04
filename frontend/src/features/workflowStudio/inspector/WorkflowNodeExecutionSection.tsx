import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeAgentConfigBody } from './WorkflowNodeAgentConfigBody'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'
import { bindingStatus, type AgentCatalogSettle } from './agentBindingStatus'
import { useCapabilityAgent } from './useAgentDefinitions'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  /** #426 review P2：workspace 级两份目录查询（published catalog +
   * agent-definitions）的 settle 信号，useAgentCatalog 下发。 */
  agentCatalogSettle: AgentCatalogSettle
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
  // #426 codex 终轮 P2：门控在节点级按 capability 计算（agentBindingStatus.
  // bindingStatus）——命中 published 即 ready，未命中须等 definitions settle。
  const { agent, isDraft } = useCapabilityAgent(props)
  const status = bindingStatus({ agent, isDraft }, props.agentCatalogSettle)
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
            bindingStatus={status}
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
