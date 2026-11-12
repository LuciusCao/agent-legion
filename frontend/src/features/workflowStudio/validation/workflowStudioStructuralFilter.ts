/* #1352（方案 A）：变更摘要的结构侧视图——从 workflowStudioChanges 拆出
 * 保体积预算。判定来源复用 isRuntimeOnlyNodeChange，与 chip 计数 / 发布
 * 门控 / 发布复核框 / 画布角标四处同一来源，不新造平行判定。 */
import {
  isRuntimeOnlyNodeChange,
  severityOrder,
  type ChangeSeverity,
  type ChangeSummaryViewModel,
  type NodeChangeGroup,
} from './workflowStudioChanges'

const INFO_ONLY_STRUCTURAL_FIELDS = new Set(['label', 'text_input'])

function structuralSeverity(
  change: NodeChangeGroup,
  fields: string[]
): ChangeSeverity {
  if (
    change.type !== 'modified' ||
    change.severity !== 'warning' ||
    !change.fields.includes('execution')
  ) {
    return change.severity
  }
  return fields.every((field) => INFO_ONLY_STRUCTURAL_FIELDS.has(field))
    ? 'info'
    : change.severity
}

/** 结构侧节点变更视图：execution-only 行整行剔除、结构/execution 混合
 * 节点剥掉 execution 字段（保留 runtime 等结构字段）并重算风险。未知或
 * 严重度依赖上下文的字段保留原档，避免误降级。 */
export function structuralNodeChangeGroups(
  summary: ChangeSummaryViewModel | null
): NodeChangeGroup[] {
  return (summary?.nodeChanges ?? [])
    .filter((change) => !isRuntimeOnlyNodeChange(change))
    .map((change) => {
      const fields = change.fields.filter((field) => field !== 'execution')
      return {
        ...change,
        fields,
        severity: structuralSeverity(change, fields),
      }
    })
}

/** 结构侧风险等级——execution-only 行（运行通道）不抬风险；后端
 * summary.risk_level 含全部行，展示面须用本口径替代。 */
export function structuralRiskLevel(
  summary: ChangeSummaryViewModel | null
): ChangeSeverity {
  if (!summary) return 'none'
  let level: ChangeSeverity = 'none'
  const consider = (severity: ChangeSeverity) => {
    // severityOrder 是降序比较器：(level, severity) > 0 ⇔ severity 更高。
    if (severityOrder(level, severity) > 0) level = severity
  }
  for (const change of structuralNodeChangeGroups(summary)) {
    consider(change.severity)
  }
  for (const group of [
    summary.edgeChanges,
    summary.intakeChanges,
    summary.metadataChanges,
    summary.riskFlags,
  ]) {
    for (const change of group) consider(change.severity)
  }
  return level
}
