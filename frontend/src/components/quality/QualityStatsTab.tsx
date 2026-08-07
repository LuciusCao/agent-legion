import { toErrorMessage } from '../../lib/queryError'
import { useQualityBatchStats } from '../../hooks/useQuality'
import type { QualityStatsGroup } from '../../api/qualityApi'
import styles from './QualityPanel.module.css'

export interface QualityStatsTabProps {
  workspaceId: string
  batchId: string
}

function formatRate(value: number | null | undefined): string {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '-'
}

function groupKey(group: QualityStatsGroup): string {
  return `${group.node_key}|${group.skill_version}|${group.provider}|${group.model}`
}

/** 单个 review 分组的 2x2 混淆矩阵（正类 = 拦截）。 */
function ReviewConfusionMatrix({ group }: { group: QualityStatsGroup }) {
  const matrix = group.confusion_matrix
  return (
    <div className={styles.matrixCard}>
      <p className={styles.matrixTitle}>
        {group.node_key} · {group.skill_version || '未标版本'} ·{' '}
        {group.model || '未知模型'}
      </p>
      {matrix ? (
        <>
          <table
            className={styles.matrixTable}
            aria-label={`${group.node_key} 混淆矩阵`}
          >
            <thead>
              <tr>
                <th />
                <th>人标 good</th>
                <th>人标 bad</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>review 放行</th>
                <td>
                  <span className={styles.matrixValue}>{matrix.tn}</span>
                  正确放行
                </td>
                <td>
                  <span className={styles.matrixValue}>{matrix.fn}</span>
                  漏放
                </td>
              </tr>
              <tr>
                <th>review 拦截</th>
                <td>
                  <span className={styles.matrixValue}>{matrix.fp}</span>
                  误杀
                </td>
                <td>
                  <span className={styles.matrixValue}>{matrix.tp}</span>
                  正确拦截
                </td>
              </tr>
            </tbody>
          </table>
          <p className={styles.matrixRates}>
            {`precision ${formatRate(matrix.precision)} · recall ${formatRate(
              matrix.recall
            )} · accuracy ${formatRate(matrix.accuracy)}`}
          </p>
        </>
      ) : (
        <p className={styles.matrixEmpty}>
          暂无已打标的可分类样本，先在「打标」页完成标注后再看矩阵。
        </p>
      )}
    </div>
  )
}

/** Tab 3：按 (node_key, skill_version, provider, model) 分组的聚合指标。 */
export function QualityStatsTab({
  workspaceId,
  batchId,
}: QualityStatsTabProps) {
  const query = useQualityBatchStats(workspaceId, batchId)
  const error = toErrorMessage(query.error)

  if (error) {
    return <p className={styles.error}>统计加载失败：{error}</p>
  }

  const groups = query.data?.groups ?? []
  const reviewGroups = groups.filter((group) =>
    group.node_key.startsWith('review_')
  )

  return (
    <>
      <div className={styles.tableWrap}>
        <table aria-label="质量统计">
          <thead>
            <tr>
              <th>节点</th>
              <th>技能版本</th>
              <th>Provider</th>
              <th>模型</th>
              <th>Runs</th>
              <th>成功率</th>
              <th>已打标</th>
              <th>Good / Bad</th>
              <th>Good 率</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={groupKey(group)}>
                <td>{group.node_key}</td>
                <td>{group.skill_version}</td>
                <td>{group.provider}</td>
                <td>{group.model}</td>
                <td>{group.runs}</td>
                <td>
                  {formatRate(group.success_rate)}（{group.succeeded}/
                  {group.runs}）
                </td>
                <td>{group.labeled}</td>
                <td>
                  {group.good} / {group.bad}
                </td>
                <td>{formatRate(group.good_rate)}</td>
              </tr>
            ))}
            {query.isLoading && (
              <tr>
                <td colSpan={9} className={styles.emptyCell}>
                  加载中…
                </td>
              </tr>
            )}
            {!query.isLoading && groups.length === 0 && (
              <tr>
                <td colSpan={9} className={styles.emptyCell}>
                  暂无统计数据
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {reviewGroups.length > 0 && (
        <section className={styles.matrixSection} aria-label="Review 混淆矩阵">
          <h3 className={styles.matrixHeading}>
            Review 混淆矩阵（正类 = 拦截）
          </h3>
          <div className={styles.matrixGrid}>
            {reviewGroups.map((group) => (
              <ReviewConfusionMatrix key={groupKey(group)} group={group} />
            ))}
          </div>
        </section>
      )}
    </>
  )
}
