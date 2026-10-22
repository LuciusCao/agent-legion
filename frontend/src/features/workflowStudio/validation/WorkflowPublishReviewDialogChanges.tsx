import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import {
  hasStructuralChanges,
  isRuntimeOnlyNodeChange,
} from './workflowStudioChanges'
import { WorkflowPublishReviewDialogRuntimeNote } from './WorkflowPublishReviewDialogRuntimeNote'
import {
  formatEdgeChange,
  formatIntakeChange,
  formatMetadataChange,
  formatNodeChange,
} from './workflowStudioChanges'
import { WorkflowPublishReviewDialogChangeCount } from './WorkflowPublishReviewDialogChangeCount'
import { WorkflowPublishReviewDialogChangeList } from './WorkflowPublishReviewDialogChangeList'
import { WorkflowPublishReviewDialogRiskChip } from './WorkflowPublishReviewDialogRiskChip'
import styles from './WorkflowPublishReviewDialog.module.css'

type Props = {
  summary: ChangeSummaryViewModel | null
  /** M49：runtime-only 确认（agent 发布请求的运行配置保存）时占位/信息条
   * 文案按「不产生新 revision」口径分拆。 */
  createsRevision?: boolean
  /** M48：草稿里尚未经运行通道保存的 runtime 键（随发布一并生效的提示）。 */
  runtimeChangedKeys?: string[]
  /** M48：本会话已经运行通道保存生效的 runtime 键（不随发布的提示）。 */
  runtimeSavedKeys?: string[]
}

/* M48 双通道：复核框只列结构 diff——execution-only 的节点变更（runtime
 * 字段）整行剔除；结构/execution 混合的节点保留行但字段清单剥掉
 * execution（其去向由下方信息条明示：已经运行通道单独生效 / 未保存的将
 * 随发布写入新版本）。 */
export function WorkflowPublishReviewDialogChanges({
  summary,
  createsRevision = true,
  runtimeChangedKeys = [],
  runtimeSavedKeys = [],
}: Props) {
  const structuralNodeChanges = (summary?.nodeChanges ?? [])
    .filter((change) => !isRuntimeOnlyNodeChange(change))
    .map((change) => ({
      ...change,
      fields: change.fields.filter((field) => field !== 'execution'),
    }))
  // 与发布门控同口径（hasStructuralChanges || riskFlags，M48；结构徽标计数
  // 仍用上方剥离了 execution 字段的 structuralNodeChanges）。
  const hasChanges =
    summary && (hasStructuralChanges(summary) || summary.riskFlags.length > 0)
  // M49：runtime-only 确认（不产生新 revision）时「没有可发布的变更」是
  // 误导——本次的内容就是运行配置保存本身。
  const noChangesText = createsRevision
    ? '没有可发布的变更'
    : '无结构变更，本次只保存运行配置'

  return (
    <>
      {!hasChanges && <p className={styles.noChanges}>{noChangesText}</p>}

      {hasChanges && (
        <>
          <div className={styles.summaryBar}>
            <WorkflowPublishReviewDialogChangeCount
              count={structuralNodeChanges.length}
              label="节点变更"
            />
            <WorkflowPublishReviewDialogChangeCount
              count={summary.edgeChanges.length}
              label="边变更"
            />
            <WorkflowPublishReviewDialogChangeCount
              count={summary.intakeChanges.length}
              label="Intake 变更"
            />
            <WorkflowPublishReviewDialogChangeCount
              count={summary.metadataChanges.length}
              label="元数据变更"
            />
            {summary.riskLevel !== 'none' && (
              <WorkflowPublishReviewDialogRiskChip
                severity={summary.riskLevel as 'info' | 'warning' | 'breaking'}
              />
            )}
          </div>

          <WorkflowPublishReviewDialogChangeList
            title="节点变更（仅结构字段）"
            items={structuralNodeChanges.map((change) => ({
              key: `node-${change.type}-${change.nodeKey}`,
              text: formatNodeChange(change),
              severity: change.severity as 'info' | 'warning' | 'breaking',
            }))}
          />

          <WorkflowPublishReviewDialogChangeList
            title="边变更"
            items={summary.edgeChanges.map((change) => ({
              key: `edge-${change.type}-${change.source}-${change.target}`,
              text: formatEdgeChange(change),
              severity: change.severity as 'info' | 'warning' | 'breaking',
            }))}
          />

          <WorkflowPublishReviewDialogChangeList
            title="Intake 变更"
            items={summary.intakeChanges.map((change) => ({
              key: `intake-${change.type}-${change.modeKey}-${change.fieldKey ?? ''}`,
              text: formatIntakeChange(change),
              severity: change.severity as 'info' | 'warning' | 'breaking',
            }))}
          />

          <WorkflowPublishReviewDialogChangeList
            title="元数据变更"
            items={summary.metadataChanges.map((change) => ({
              key: `metadata-${change.field}`,
              text: formatMetadataChange(change),
              severity: change.severity as 'info' | 'warning' | 'breaking',
            }))}
          />

          <WorkflowPublishReviewDialogChangeList
            title="风险提示"
            items={summary.riskFlags.map((flag) => ({
              key: `risk-${flag.code}`,
              text: flag.message,
              severity: flag.severity as 'info' | 'warning' | 'breaking',
            }))}
          />
        </>
      )}

      <WorkflowPublishReviewDialogRuntimeNote
        createsRevision={createsRevision}
        runtimeChangedKeys={runtimeChangedKeys}
        runtimeSavedKeys={runtimeSavedKeys}
      />
    </>
  )
}
