import type { DagNodeData } from './DagNode'
import styles from './DagNodeDefinitionMeta.module.css'

const TOPOLOGY_LABEL = {
  start: '起点',
  entry: '入口',
  branch: '分支',
  terminal: '终点',
}

export function DagNodeDefinitionMeta({ data }: { data: DagNodeData }) {
  return (
    <>
      {data.topologyBadges && data.topologyBadges.length > 0 && (
        <div className={styles.topologyBadges}>
          {data.topologyBadges.map((badge) => (
            <span key={badge}>{TOPOLOGY_LABEL[badge]}</span>
          ))}
        </div>
      )}
      {data.nodeKey && data.capability && (
        <div className={styles.definitionMeta}>
          <span title={`节点 Key: ${data.nodeKey}`}>Key · {data.nodeKey}</span>
          <span title={`能力 Key: ${data.capability}`}>
            能力 · {data.capability}
          </span>
        </div>
      )}
    </>
  )
}
