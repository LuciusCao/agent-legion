import type { WorkflowDefinitionRecord } from '../../types'
import styles from './WorkflowNodeOutline.module.css'

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
    <section aria-label="Workflow nodes" className={styles.section}>
      <h2 className={styles.title}>节点</h2>
      {!workflow ? (
        <p className={styles.meta}>暂无节点</p>
      ) : (
        <ul className={styles.list}>
          {workflow.nodes.map((node) => (
            <li key={node.key}>
              <button
                type="button"
                className={styles.item}
                aria-pressed={node.key === selectedNodeKey}
                onClick={() => onSelectNode(node.key)}
              >
                <span className={styles.label}>{node.label}</span>
                <span className={styles.meta}>
                  {node.capability} · 输入 {node.inputs.length} / 输出{' '}
                  {node.outputs.length}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
