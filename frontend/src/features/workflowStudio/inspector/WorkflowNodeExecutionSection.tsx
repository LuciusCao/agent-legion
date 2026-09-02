import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { WorkflowNodeRecord } from '../../../types'
import {
  WorkflowNodeAgentConfigBody,
  WorkflowNodeApprovalSection,
} from './WorkflowNodeAgentConfigBody'
import { WorkflowNodeAgentEditor } from './WorkflowNodeAgentEditor'
import { useCapabilityAgent } from './useAgentDefinitions'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  /** type=code 节点「切换为 Agent 执行」：把草稿 YAML 的节点 type 改为
   * agent（返回是否改写成功）。 */
  onSwitchToAgent?: () => boolean
  readOnly?: boolean
}
export function WorkflowNodeExecutionSection(props: Props) {
  const { node } = props
  // #387：draft-only Agent 回落 agent-definitions（useCapabilityAgent；
  // 路由 workspaceId 与 openAgent 的 pendingAgentId 也在该 hook 内消费）。
  const { agent, isDraft } = useCapabilityAgent(props)
  // EXEC-APPROVAL-001：审批门不 dispatch，专属区块（无 Agent 编辑入口）。
  if (node.node_type === 'approval') return <WorkflowNodeApprovalSection />
  // #284：节点类型由显式 node_type 判定；agentCatalog 仅用于按 capability
  // 找 Agent 定义做展示/编辑，不再参与类型判定。
  const isAgentNode = node.node_type === 'agent'
  return (
    <section className={inspectorStyles.section} aria-label="节点执行能力">
      <div className={inspectorStyles.sectionTitle}>
        {isAgentNode ? 'Agent 配置' : '代码节点'}
      </div>
      {isAgentNode ? (
        <WorkflowNodeAgentConfigBody
          node={node}
          agentDefinition={agent}
          isDraft={isDraft}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
      ) : (
        // type=code：内置 code 池执行，无绑定可配。
        <div className={inspectorStyles.empty}>内置 code 池执行</div>
      )}
      <WorkflowNodeAgentEditor
        agentId={agent?.id ?? null}
        capability={node.capability}
        nodeType={isAgentNode ? 'agent' : 'code'}
        onSwitchToAgent={props.onSwitchToAgent}
        readOnly={props.readOnly}
      />
    </section>
  )
}
