import styles from './NodeRunsTable.module.css'

export interface NodeRun {
  nodeKey: string
  nodeLabel: string
  status: string
  time: string
  duration: string
}

export interface NodeRunsTableProps {
  runs: NodeRun[]
}

export function NodeRunsTable({ runs }: NodeRunsTableProps) {
  return (
    <table className={styles.table}>
      <thead>
        <tr className={styles.headerRow}>
          <th>节点</th>
          <th>状态</th>
          <th>时间</th>
          <th>耗时</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.nodeKey} data-run={run.nodeKey}>
            <td>{run.nodeLabel}</td>
            <td>{run.status}</td>
            <td>{run.time}</td>
            <td>{run.duration}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
