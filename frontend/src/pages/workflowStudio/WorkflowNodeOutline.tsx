import type { WorkflowDefinitionRecord } from '../../types'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
  onSelectNode: (nodeKey: string) => void
}

export function WorkflowNodeOutline({
  workflow,
  selectedNodeKey,
  onSelectNode,
}: Props) {
  return (
    <section aria-label="Workflow nodes">
      <h2>节点</h2>
      {!workflow ? (
        <p>暂无节点</p>
      ) : (
        <ul>
          {workflow.nodes.map((node) => (
            <li key={node.key}>
              <button
                type="button"
                aria-pressed={node.key === selectedNodeKey}
                onClick={() => onSelectNode(node.key)}
              >
                <span>{node.label}</span>
                <span>{node.capability}</span>
                <span>
                  输入 {node.inputs.length} / 输出 {node.outputs.length}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
