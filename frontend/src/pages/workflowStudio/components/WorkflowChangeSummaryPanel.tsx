import type { components } from '../../../generated/api'
import {
  buildChangeSummary,
  ChangeSeverity,
  ChangeSummaryViewModel,
  EdgeChangeGroup,
  formatEdgeChange,
  formatIntakeChange,
  formatMetadataChange,
  formatNodeChange,
  groupCompareErrors,
  IntakeChangeGroup,
  MetadataChangeGroup,
  NodeChangeGroup,
  RiskFlagGroup,
  severityLabel,
  severityVariant,
} from '../workflowStudioChanges'
import styles from './WorkflowChangeSummaryPanel.module.css'

type CompareError = components['schemas']['WorkflowDraftCompareError']

type Props = {
  summary: ChangeSummaryViewModel | null
  loading: boolean
  errors: CompareError[] | null
  onSelectNode?: (nodeKey: string) => void
}

function classForSeverity(severity: ChangeSeverity): string {
  const variant = severityVariant(severity)
  const variantClass = {
    default: styles.badgeDefault,
    info: styles.badgeInfo,
    warning: styles.badgeWarning,
    error: styles.badgeError,
  }[variant]
  return `${styles.badge} ${variantClass}`
}

function ChangeBadge({ severity }: { severity: ChangeSeverity }) {
  return (
    <span className={classForSeverity(severity)}>
      {severityLabel(severity)}
    </span>
  )
}

function NodeItem({
  change,
  onSelectNode,
}: {
  change: NodeChangeGroup
  onSelectNode?: (nodeKey: string) => void
}) {
  const text = formatNodeChange(change)
  return (
    <li className={styles.item}>
      <span
        className={`${styles.itemText} ${onSelectNode ? styles.clickableNode : ''}`}
        onClick={() => onSelectNode?.(change.nodeKey)}
        title={text}
      >
        {text}
      </span>
      <ChangeBadge severity={change.severity} />
    </li>
  )
}

function EdgeItem({ change }: { change: EdgeChangeGroup }) {
  const text = formatEdgeChange(change)
  return (
    <li className={styles.item}>
      <span className={styles.itemText} title={text}>
        {text}
      </span>
      <ChangeBadge severity={change.severity} />
    </li>
  )
}

function IntakeItem({ change }: { change: IntakeChangeGroup }) {
  const text = formatIntakeChange(change)
  return (
    <li className={styles.item}>
      <span className={styles.itemText} title={text}>
        {text}
      </span>
      <ChangeBadge severity={change.severity} />
    </li>
  )
}

function MetadataItem({ change }: { change: MetadataChangeGroup }) {
  const text = formatMetadataChange(change)
  return (
    <li className={styles.item}>
      <span className={styles.itemText} title={text}>
        {text}
      </span>
      <ChangeBadge severity={change.severity} />
    </li>
  )
}

function RiskItem({ flag }: { flag: RiskFlagGroup }) {
  return (
    <li className={styles.item}>
      <span className={styles.itemText} title={flag.message}>
        {flag.message}
      </span>
      <ChangeBadge severity={flag.severity} />
    </li>
  )
}

function ErrorGroups({
  errors,
  onSelectNode,
}: {
  errors: CompareError[]
  onSelectNode?: (nodeKey: string) => void
}) {
  const groups = groupCompareErrors(errors)
  return (
    <>
      {groups.map((group) => (
        <div key={group.category} className={styles.errorGroup}>
          <h4 className={styles.errorGroupTitle}>{group.categoryLabel}</h4>
          <ul className={styles.list}>
            {group.errors.map((error, index) => (
              <li
                key={`${group.category}-${index}`}
                className={styles.errorItem}
              >
                <span className={styles.errorMessage}>{error.message}</span>
                {error.node_key && onSelectNode && (
                  <button
                    type="button"
                    className={styles.clickableNode}
                    onClick={() => onSelectNode(error.node_key!)}
                  >
                    节点: {error.node_key}
                  </button>
                )}
                {(error.line || error.column) && (
                  <span className={styles.errorLocation}>
                    位置: {error.line ?? '-'} 行 {error.column ?? '-'} 列
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  )
}

export function WorkflowChangeSummaryPanel({
  summary,
  loading,
  errors,
  onSelectNode,
}: Props) {
  const viewModel = summary ?? buildChangeSummary(null)
  const hasErrors = errors && errors.length > 0

  if (loading) {
    return (
      <section aria-label="Workflow change summary" className={styles.panel}>
        <h2 className={styles.title}>变更摘要</h2>
        <div className={styles.loadingState}>
          <span className={styles.spinner} aria-hidden="true" />
          <span>正在对比...</span>
        </div>
      </section>
    )
  }

  if (hasErrors) {
    return (
      <section aria-label="Workflow change summary" className={styles.panel}>
        <h2 className={styles.title}>变更摘要</h2>
        <div
          className={styles.emptyState}
          style={{ background: '#fee2e2', color: '#b91c1c' }}
        >
          YAML 无法解析，请先修正错误
        </div>
        <ErrorGroups errors={errors!} onSelectNode={onSelectNode} />
      </section>
    )
  }

  const hasChanges =
    viewModel.nodeChanges.length > 0 ||
    viewModel.edgeChanges.length > 0 ||
    viewModel.intakeChanges.length > 0 ||
    viewModel.metadataChanges.length > 0 ||
    viewModel.riskFlags.length > 0

  return (
    <section aria-label="Workflow change summary" className={styles.panel}>
      <h2 className={styles.title}>变更摘要</h2>
      {!hasChanges ? (
        <div className={styles.emptyState}>
          已同步 — 当前 YAML 与 active revision 一致
        </div>
      ) : (
        <>
          <div className={styles.emptyState}>
            风险等级: {viewModel.severityLabel}
          </div>
          {viewModel.nodeChanges.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>节点变更</h3>
              <ul className={styles.list}>
                {viewModel.nodeChanges.map((change) => (
                  <NodeItem
                    key={`node-${change.type}-${change.nodeKey}`}
                    change={change}
                    onSelectNode={onSelectNode}
                  />
                ))}
              </ul>
            </div>
          )}
          {viewModel.edgeChanges.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>边变更</h3>
              <ul className={styles.list}>
                {viewModel.edgeChanges.map((change) => (
                  <EdgeItem
                    key={`edge-${change.type}-${change.source}-${change.target}`}
                    change={change}
                  />
                ))}
              </ul>
            </div>
          )}
          {viewModel.intakeChanges.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>Intake 变更</h3>
              <ul className={styles.list}>
                {viewModel.intakeChanges.map((change) => (
                  <IntakeItem
                    key={`intake-${change.type}-${change.modeKey}-${change.fieldKey ?? ''}`}
                    change={change}
                  />
                ))}
              </ul>
            </div>
          )}
          {viewModel.metadataChanges.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>元数据变更</h3>
              <ul className={styles.list}>
                {viewModel.metadataChanges.map((change) => (
                  <MetadataItem
                    key={`metadata-${change.field}`}
                    change={change}
                  />
                ))}
              </ul>
            </div>
          )}
          {viewModel.riskFlags.length > 0 && (
            <div className={styles.section}>
              <h3 className={styles.sectionTitle}>风险提示</h3>
              <ul className={styles.list}>
                {viewModel.riskFlags.map((flag) => (
                  <RiskItem key={`risk-${flag.code}`} flag={flag} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  )
}
