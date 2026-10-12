import type { WorkflowDefinitionRecord } from '../../../types'
import { useMemo } from 'react'
import { WorkflowInspectorEmptyState } from './WorkflowInspectorOverviewFallback'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { inspectorNodeDetails } from './workflowStudioInspectorDetails'
import { WorkflowNodeInspectorBody } from './WorkflowNodeInspectorBody'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  compareSummary?: ChangeSummaryViewModel | null
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspector(props: Props) {
  const { workflow, selectedNodeKey, definitionYaml, compareSummary } = props
  // 草稿 YAML 优先（#405：已发布节点切型后选择器须反映草稿类型）；
  // 其次基线 workflow；最后 compareSummary（YAML 里没有 start 时 loader
  // 注入的合成 start ghost）。details 链含完整草稿转换（形状守卫，#405
  // codex P1），按输入 memo 化避免每次渲染重解析。
  const details = useMemo(
    () =>
      inspectorNodeDetails(
        { workflow, definitionYaml, compareSummary },
        selectedNodeKey
      ),
    [workflow, definitionYaml, compareSummary, selectedNodeKey]
  )
  if (!workflow && !details)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) return <WorkflowInspectorEmptyState />
  return (
    <WorkflowNodeInspectorBody
      details={details}
      definitionYaml={props.definitionYaml}
      setDefinitionYaml={props.setDefinitionYaml}
      readOnly={props.readOnly}
      onClose={props.onClose}
    />
  )
}
