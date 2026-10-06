import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeLegacyAgentBody } from './WorkflowNodeLegacyAgentBody'
import {
  WorkflowNodeProfileBody,
  useNodeRuntimeInfo,
} from './WorkflowNodeProfileBody'
import inspectorStyles from './WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// #392 Phase 2：类型注册表只把本 section 挂在 code / agent 类型上，
// approval 由专属的 WorkflowNodeApprovalConfigSection 承载，节点内不
// 再需要类型分叉。
export function WorkflowNodeExecutionSection(props: Props) {
  const { node } = props
  // #935（#440 P3）：节点（或 workflow 顶层）声明了 execution.runtime 即
  // 自含执行档案——编辑全部写进节点草稿。#1079（#440 P3b）：Agent 定义
  // 不再参与节点编辑（不解析 capability → Agent、不渲染 AgentEditor）。
  const runtimeInfo = useNodeRuntimeInfo(node, props.definitionYaml)
  // 防御：#392 Phase 2 起注册表只把本 section 挂在 code/agent 类型上，
  // approval 由 WorkflowNodeApprovalConfigSection 承载。直接喂 approval
  // 时渲染空（不落入误导性的「代码节点」文案）。hooks 在早退前调用。
  if (node.node_type === 'approval') return null
  // #284/#392：节点类型由显式 node_type 判定。code 节点的类型变更走头部
  // 类型选择器，不在此处长出 Agent 入口。
  const isAgentNode = node.node_type === 'agent'
  return (
    <section className={inspectorStyles.section} aria-label="节点执行能力">
      <div className={inspectorStyles.sectionTitle}>
        {isAgentNode ? 'Agent 配置' : '代码节点'}
      </div>
      {isAgentNode && runtimeInfo.effectiveRuntime ? (
        <WorkflowNodeProfileBody
          node={node}
          runtimeInfo={runtimeInfo}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
      ) : isAgentNode ? (
        <WorkflowNodeLegacyAgentBody
          node={node}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          readOnly={props.readOnly}
        />
      ) : (
        // type=code：内置 code 池执行，无绑定可配。
        <div className={inspectorStyles.empty}>内置 code 池执行</div>
      )}
    </section>
  )
}
