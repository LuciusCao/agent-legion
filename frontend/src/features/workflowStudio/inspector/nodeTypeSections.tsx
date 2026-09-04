import type { ComponentType } from 'react'
import type { SwitchableNodeType } from '../shared/workflowStudioYamlDraft.nodeType'
import { WorkflowNodeCodeSection } from '../code-editor/WorkflowNodeCodeSection'
import { WorkflowNodeConfigSchemaSection } from './WorkflowNodeConfigSchemaSection'
import { WorkflowNodeConfigSection } from './WorkflowNodeConfigSection'
import { WorkflowNodeDataContractSection } from './WorkflowNodeDataContractSection'
import { WorkflowNodeDependencySection } from './WorkflowNodeDependencySection'
import { WorkflowNodeEditorSection } from './WorkflowNodeEditorSection'
import { WorkflowNodeExecutionSection } from './WorkflowNodeExecutionSection'
import { WorkflowNodeApprovalConfigSection } from './WorkflowNodeApprovalConfigSection'
import type { InspectorSectionProps } from './WorkflowNodeInspectorSections'

// 节点类型的 section 注册表（#392 Phase 2）：类型是一等概念——每个类型
// 声明自己的 section 序列，新增类型 = 注册表加一行，不再触碰已有
// section。section 组件全部复用现有组件，只是组合关系从「渲染全部 +
// 内部分叉」收敛为「按类型挑序列」；组件只收窄到「单类型可渲染」的
// props 切片（多数 section 不需要 agentCatalog/definitionYaml 全量）。
type SectionSpec = {
  /** 该类型的 section 渲染序列（顺序即展示顺序）。 */
  sections: ComponentType<InspectorSectionProps>[]
}

export const NODE_TYPE_SECTIONS: Record<SwitchableNodeType, SectionSpec> = {
  // code：基本设置 → 生效 schema（code = 声明 schema）→ 执行能力 →
  // 节点代码 → 节点配置（版本值 + 运行时覆盖双通道，#418）→ 数据契约 → 依赖。
  code: {
    sections: [
      EditorSection,
      ConfigSchemaSection,
      ExecutionSection,
      CodeSection,
      NodeConfigSection,
      DataContractSection,
      DependencySection,
    ],
  },
  // agent：基本设置 → 执行能力（Agent 配置 + 编辑入口）→
  // 节点配置（仅运行时覆盖通道——schema 归 Agent Definition）→
  // 数据契约 → 依赖。Agent 的有效 config_schema 归 Agent
  // Definition 管理，不渲染节点 YAML 的 schema 编辑区（#406）。
  agent: {
    sections: [
      EditorSection,
      ExecutionSection,
      NodeConfigSection,
      DataContractSection,
      DependencySection,
    ],
  },
  // approval：基本设置 → 审批门配置 → 数据契约 → 依赖。无执行能力/
  // 节点代码/schema/节点配置段（不 dispatch，config 白名单只此一处）。
  approval: {
    sections: [
      EditorSection,
      ApprovalConfigSection,
      DataContractSection,
      DependencySection,
    ],
  },
}

// —— 适配层：注册表引用的组件与 InspectorSectionProps 的对齐 ————

// 大多数 section 只消费 props 切片（node / definitionYaml / readOnly），
// 适配器剥掉多余 props 保持类型合法（多余 prop 传下去也无害，但显式
// 收窄让 section 的依赖可审计）。

function EditorSection(props: InspectorSectionProps) {
  return (
    <WorkflowNodeEditorSection
      node={props.details.node}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
    />
  )
}
function ConfigSchemaSection(props: InspectorSectionProps) {
  return (
    <WorkflowNodeConfigSchemaSection
      key={`config-schema-${props.details.node.key}`}
      node={props.details.node}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
    />
  )
}
function ExecutionSection(props: InspectorSectionProps) {
  return <WorkflowNodeExecutionSection node={props.details.node} {...props} />
}
function CodeSection(props: InspectorSectionProps) {
  return (
    <WorkflowNodeCodeSection
      key={`code-${props.details.node.key}`}
      node={props.details.node}
      readOnly={props.readOnly}
    />
  )
}
function NodeConfigSection(props: InspectorSectionProps) {
  return (
    <WorkflowNodeConfigSection
      key={`config-${props.details.node.key}`}
      node={props.details.node}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
    />
  )
}
function DataContractSection(props: InspectorSectionProps) {
  return (
    <WorkflowNodeDataContractSection
      key={`data-contract-${props.details.node.key}`}
      node={props.details.node}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
    />
  )
}
function DependencySection(props: InspectorSectionProps) {
  return (
    <WorkflowNodeDependencySection
      key={`dependencies-${props.details.node.key}`}
      details={props.details}
    />
  )
}
function ApprovalConfigSection(props: InspectorSectionProps) {
  return (
    <WorkflowNodeApprovalConfigSection
      key={`approval-config-${props.details.node.key}`}
      node={props.details.node}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
    />
  )
}
