import {
  formatQualityDateTime,
  QualityArtifactView,
  QualityLabelHistory,
} from './QualityArtifactView'
import { QualityLabelForm } from './QualityLabelForm'
import { QualityReplaySection } from './QualityReplaySection'
import { toErrorMessage } from '../../lib/queryError'
import { useQualityItemDetail } from '../../hooks/useQuality'
import styles from './QualityPanel.module.css'

export interface QualityItemDetailPanelProps {
  workspaceId: string
  itemId: string
}

/** 打标 Tab 右侧：item 快照、产物内容、Replay、打标表单与标签历史。 */
export function QualityItemDetailPanel({
  workspaceId,
  itemId,
}: QualityItemDetailPanelProps) {
  const query = useQualityItemDetail(workspaceId, itemId)

  if (query.isLoading) return <p className={styles.muted}>加载中…</p>
  if (query.error) {
    return (
      <p className={styles.error}>
        样本详情加载失败：{toErrorMessage(query.error)}
      </p>
    )
  }

  const detail = query.data
  if (!detail) return null
  const { item, artifacts, labels } = detail

  const snapshotRows: [string, string][] = [
    ['节点', item.node_key],
    ['Capability', item.capability],
    ['技能版本', item.skill_version],
    [
      'Agent 版本',
      item.agent_version != null ? String(item.agent_version) : '-',
    ],
    ['Provider / 模型', `${item.provider} / ${item.model}`],
    ['运行状态', item.run_status],
    ['失败类别', item.failure_category || '-'],
    ['失败详情', item.failure_detail || '-'],
    ['Job', item.job_id],
    ['Node Run', String(item.node_run_id)],
    ['时间', formatQualityDateTime(item.created_at)],
  ]

  return (
    <div className={styles.detail}>
      <section aria-label="样本快照">
        <h3>样本快照</h3>
        <dl className={styles.snapshotGrid}>
          {snapshotRows.map(([label, value]) => (
            <div key={label} className={styles.snapshotRow}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-label="节点产物">
        <h3>节点产物</h3>
        {artifacts.length === 0 && (
          <p className={styles.muted}>该样本没有产物内容</p>
        )}
        {artifacts.map((artifact) => (
          <QualityArtifactView key={artifact.name} artifact={artifact} />
        ))}
      </section>

      <section aria-label="Replay">
        <h3>Replay</h3>
        <QualityReplaySection
          workspaceId={workspaceId}
          itemId={itemId}
          originalArtifacts={artifacts}
          originalAgentVersion={item.agent_version}
        />
      </section>

      <section aria-label="打标">
        <h3>打标</h3>
        <QualityLabelForm workspaceId={workspaceId} itemId={itemId} />
      </section>

      <section aria-label="标签历史">
        <h3>标签历史</h3>
        <QualityLabelHistory labels={labels} />
      </section>
    </div>
  )
}
