import styles from './MiniDag.module.css'

export interface MiniDagNode {
  key: string
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  duration?: number
}

export interface MiniDagProps {
  nodes: MiniDagNode[]
}

const ICONS: Record<string, string> = {
  pending: 'radio_button_unchecked',
  running: 'sync',
  completed: 'check_circle',
  failed: 'error',
}

export function MiniDag({ nodes }: MiniDagProps) {
  return (
    <div className={styles.track}>
      {nodes.map((node, idx) => (
        <div key={node.key} className={styles.nodeGroup}>
          <div
            data-node={node.key}
            className={`${styles.node} ${styles[node.status]}`}
          >
            <md-icon>{ICONS[node.status]}</md-icon>
            <span className={styles.label}>{node.label}</span>
            {typeof node.duration === 'number' && (
              <span className={styles.duration}>{node.duration}s</span>
            )}
          </div>
          {idx < nodes.length - 1 && (
            <md-icon class={styles.arrow}>arrow_forward</md-icon>
          )}
        </div>
      ))}
    </div>
  )
}
