import type { WorkflowDefinitionRecord } from '../../types'
import { buildTopologyOrder } from './workflowStudioTopology'
import { WorkflowNodeOutlineItem } from './components/WorkflowNodeOutlineItem'
import { WorkflowNodeOutlineWarnings } from './components/WorkflowNodeOutlineWarnings'
import itemStyles from './WorkflowNodeOutlineItem.module.css'
import styles from './WorkflowNodeOutline.module.css'
type Props = {
  workflow: WorkflowDefinitionRecord | null
  selectedNodeKey: string | null
  onSelectNode: (nodeKey: string) => void
  changedNodeKeys?: Set<string>
}

export function WorkflowNodeOutline({
  workflow,
  selectedNodeKey,
  onSelectNode,
  changedNodeKeys,
}: Props) {
  const topology = buildTopologyOrder(workflow)
  return (
    <section aria-label="Workflow nodes" className={styles.section}>
      <h2 className={styles.title}>节点</h2>
      {!workflow ? (
        <p className={itemStyles.meta}>暂无节点</p>
      ) : (
        <>
          <ul className={styles.list}>
            {[...topology.order, ...topology.disconnected].map((nodeKey) => (
              <WorkflowNodeOutlineItem
                key={nodeKey}
                workflow={workflow}
                nodeKey={nodeKey}
                selected={nodeKey === selectedNodeKey}
                changedNodeKeys={changedNodeKeys}
                onSelect={onSelectNode}
              />
            ))}
          </ul>
          <WorkflowNodeOutlineWarnings topology={topology} />
        </>
      )}
    </section>
  )
}
