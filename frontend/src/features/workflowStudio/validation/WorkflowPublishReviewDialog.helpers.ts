import type { ChangeSummaryViewModel } from './workflowStudioChanges'

export function hasCompareSummaryChanges(
  summary: ChangeSummaryViewModel | null
): boolean {
  if (!summary) return false
  return (
    summary.nodeChanges.length > 0 ||
    summary.edgeChanges.length > 0 ||
    summary.intakeChanges.length > 0 ||
    summary.metadataChanges.length > 0 ||
    summary.riskFlags.length > 0
  )
}

/** M49 确认门禁（双通道）：结构 diff 或待生效 runtime 键任一存在即可确认。
 * runtime-only 的 agent 发布请求（tools/requires_labels 自 M47 不进 compare）
 * compare 摘要为空——只看摘要会把确认永久禁用（通道死端）；确认端点对此按
 * runtime-only 原地生效 + 追加配置版本处理。 */
export function canConfirmPublish(
  summary: ChangeSummaryViewModel | null,
  runtimeChangedKeys: string[]
): boolean {
  return hasCompareSummaryChanges(summary) || runtimeChangedKeys.length > 0
}
