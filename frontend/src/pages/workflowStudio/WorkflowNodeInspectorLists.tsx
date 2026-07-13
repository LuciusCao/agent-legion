import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { conditionLabel } from './workflowStudioModel'
import styles from './WorkflowNodeInspector.module.css'

export function ItemList({ items }: { items: string[] }) {
  if (items.length === 0) return <span className={styles.empty}>无</span>
  return (
    <ul className={styles.list}>
      {items.map((item) => (
        <li key={item} className={styles.listItem}>
          {item}
        </li>
      ))}
    </ul>
  )
}

export function EdgeList({
  edges,
  nodeKey,
  outgoing,
}: {
  edges: SelectedWorkflowNodeDetails['incoming']
  nodeKey: string
  outgoing: boolean
}) {
  if (edges.length === 0) return <span className={styles.empty}>无</span>
  return (
    <ul className={styles.edgeList}>
      {edges.map((edge) => {
        const label = outgoing ? conditionLabel(edge.condition) : ''
        const left = outgoing ? nodeKey : edge.source
        const right = outgoing ? edge.target : nodeKey
        return (
          <li key={`${edge.source}-${edge.target}`} className={styles.edgeItem}>
            <span>{left}</span>
            <span className={styles.edgeArrow}>→</span>
            <span>{right}</span>
            {label && <span className={styles.condition}>({label})</span>}
          </li>
        )
      })}
    </ul>
  )
}
