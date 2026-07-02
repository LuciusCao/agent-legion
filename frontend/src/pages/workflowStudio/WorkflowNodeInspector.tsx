import type { WorkflowDefinitionRecord } from '../../types'
import { conditionLabel, selectedNodeDetails } from './workflowStudioModel'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
}

export function WorkflowNodeInspector({ workflow, selectedNodeKey }: Props) {
  const details = selectedNodeDetails(workflow, selectedNodeKey)
  if (!workflow)
    return <section aria-label="Workflow inspector">未加载 workflow</section>
  if (!details) {
    return (
      <section aria-label="Workflow inspector">
        <h2>工作流概览</h2>
        <p>节点 {workflow.nodes.length}</p>
        <p>连线 {workflow.edges.length}</p>
        <p>Intake {workflow.intake.modes.length}</p>
      </section>
    )
  }
  return (
    <section aria-label="Workflow inspector">
      <h2>{details.node.label}</h2>
      <dl>
        <dt>Key</dt>
        <dd>{details.node.key}</dd>
        <dt>Capability</dt>
        <dd>{details.node.capability}</dd>
        <dt>Inputs</dt>
        <dd>{details.node.inputs.join(', ') || '无'}</dd>
        <dt>Outputs</dt>
        <dd>{details.node.outputs.join(', ') || '无'}</dd>
        <dt>Incoming</dt>
        <dd>
          {details.incoming.map((edge) => edge.source).join(', ') || '无'}
        </dd>
        <dt>Outgoing</dt>
        <dd>
          {details.outgoing
            .map((edge) => {
              const label = conditionLabel(edge.condition)
              return label ? `${edge.target} (${label})` : edge.target
            })
            .join(', ') || '无'}
        </dd>
        <dt>Terminal</dt>
        <dd>{details.node.terminal?.outcome ?? '无'}</dd>
        <dt>Executor</dt>
        <dd>执行器绑定校验将在发布时完成</dd>
      </dl>
    </section>
  )
}
