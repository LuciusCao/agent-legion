import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { conditionLabel } from './workflowStudioModel'
import styles from './WorkflowNodeInspector.module.css'

type Props = { details: SelectedWorkflowNodeDetails; readOnly?: boolean }

function ItemList({ items }: { items: string[] }) {
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

function EdgeList({
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

export function WorkflowNodeInspectorBody({ details }: Props) {
  const { node, incoming, outgoing } = details
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <h2 className={styles.title}>{node.label}</h2>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>标识</div>
        <div className={styles.value}>{node.key}</div>
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>能力</div>
        <div className={styles.value}>{node.capability}</div>
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          输入产物 ({node.inputs.length})
        </div>
        <ItemList items={node.inputs} />
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>
          输出产物 ({node.outputs.length})
        </div>
        <ItemList items={node.outputs} />
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>上游</div>
        <EdgeList edges={incoming} nodeKey={node.key} outgoing={false} />
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>下游</div>
        <EdgeList edges={outgoing} nodeKey={node.key} outgoing={true} />
      </div>
      {node.terminal && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Terminal Outcome</div>
          <span className={styles.outcome}>{node.terminal.outcome}</span>
        </div>
      )}
    </section>
  )
}
