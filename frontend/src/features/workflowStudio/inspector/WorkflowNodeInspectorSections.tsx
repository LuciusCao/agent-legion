import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import { NODE_TYPE_SECTIONS } from './nodeTypeSections'
import { WorkflowNodeStartSection } from './WorkflowNodeStartSection'

export type InspectorSectionProps = {
  details: SelectedWorkflowNodeDetails
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// section 组合的唯一事实源是 nodeTypeSections 的类型注册表（#392
// Phase 2）：非 start 类型查表渲染该类型声明的 section 序列；start
// 节点携带入口契约、永不执行，只渲染契约段（后端会 404 其节点代码
// 端点）。遗留 `type: node` / 缺省在 record/ghost 解析层已归一化 code。
export function WorkflowNodeInspectorSections(props: InspectorSectionProps) {
  const { node } = props.details
  if (node.node_type === 'start') {
    return <WorkflowNodeStartSection {...props} />
  }
  const spec =
    node.node_type === 'agent' || node.node_type === 'approval'
      ? NODE_TYPE_SECTIONS[node.node_type]
      : NODE_TYPE_SECTIONS.code
  return (
    <>
      {spec.sections.map((Section) => (
        <Section key={Section.name} {...props} />
      ))}
    </>
  )
}
