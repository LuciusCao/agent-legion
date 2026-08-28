import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { WorkflowInspectorEmptyState } from './WorkflowInspectorOverviewFallback'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { inspectorNodeDetails } from './workflowStudioInspectorDetails'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'
import { parseWorkflowKey } from '../shared/workflowStudioYamlDraft.parse'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  agentCatalog: AgentDefinition[]
  selectedNodeKey: string | null
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  compareSummary?: ChangeSummaryViewModel | null
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspector(props: Props) {
  const { workflow, selectedNodeKey } = props
  // 基线节点优先；其次草稿 YAML（compare 叠加的 draft-only ghost）；最后
  // compareSummary（YAML 里没有 start 时 loader 注入的合成 start ghost）。
  const details = inspectorNodeDetails(props, selectedNodeKey)
  if (!workflow && !details)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) return <WorkflowInspectorEmptyState />
  return (
    <WorkflowNodeInspectorBody
      details={details}
      agentCatalog={props.agentCatalog}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      workflowKey={
        workflow?.key ?? parseWorkflowKey(props.definitionYaml) ?? ''
      }
      readOnly={props.readOnly}
      onClose={props.onClose}
    />
  )
}
