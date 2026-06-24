export type ReviewDecision = {
  id: string
  decision: 'approved' | 'rejected' | 'needs_revision' | 'unknown'
  reason?: string
}

export type ReviewSummary = {
  approved: number
  rejected: number
  warnings: string[]
}

export type ReviewReport = {
  name: string
  title: string
  summary: ReviewSummary
  decisions: ReviewDecision[]
  raw?: unknown
}

const REVIEW_ARTIFACTS: Record<string, string> = {
  'key_info_review_report.json': '审核关键信息',
  'possible_errors_review_report.json': '审核可能审题错误',
  'review_result.json': '内容审核',
}

export function isReviewArtifact(name: string): boolean {
  return name in REVIEW_ARTIFACTS
}

function normalizeSummary(data: Record<string, unknown>): ReviewSummary {
  return {
    approved: typeof data.approved_count === 'number' ? data.approved_count : 0,
    rejected: typeof data.rejected_count === 'number' ? data.rejected_count : 0,
    warnings: Array.isArray(data.warnings)
      ? data.warnings.filter((w): w is string => typeof w === 'string')
      : [],
  }
}

function parseDecisionList(items: unknown, idKey: string): ReviewDecision[] {
  const decisions: ReviewDecision[] = []
  if (!Array.isArray(items)) return decisions
  items.forEach((item) => {
    if (!item || typeof item !== 'object') return
    const d = item as Record<string, unknown>
    const rawDecision = String(d.decision ?? 'unknown')
    const decision =
      rawDecision === 'approved' ||
      rawDecision === 'rejected' ||
      rawDecision === 'needs_revision'
        ? rawDecision
        : 'unknown'
    decisions.push({
      id: String(d[idKey] ?? 'unknown'),
      decision,
      reason: d.reason ? String(d.reason) : undefined,
    })
  })
  return decisions
}

function normalizeStatus(status: string): ReviewDecision['decision'] {
  if (status === 'approved') return 'approved'
  if (status === 'rejected') return 'rejected'
  if (status === 'needs_revision') return 'needs_revision'
  return 'unknown'
}

function normalizeContentReview(data: Record<string, unknown>) {
  const status = String(data.review_status ?? 'unknown')
  const decisions: ReviewDecision[] = []
  if (Array.isArray(data.details)) {
    data.details.forEach((item: unknown, index: number) => {
      if (!item || typeof item !== 'object') return
      const obj = item as Record<string, unknown>
      const itemStatus = String(obj.result ?? obj.review_status ?? 'unknown')
      decisions.push({
        id: String(obj.item ?? obj.id ?? index),
        decision: normalizeStatus(itemStatus),
        reason: obj.reason ? String(obj.reason) : undefined,
      })
    })
  }
  if (decisions.length === 0 && (data.review_status || data.review_msg)) {
    decisions.push({
      id: 'overall',
      decision: normalizeStatus(status),
      reason: data.review_msg ? String(data.review_msg) : undefined,
    })
  }
  const approved = decisions.filter((d) => d.decision === 'approved').length
  const rejected = decisions.filter((d) => d.decision === 'rejected').length
  return {
    summary: { approved, rejected, warnings: [] },
    decisions,
  }
}

export function parseReviewReport(name: string, content: string): ReviewReport {
  const title = REVIEW_ARTIFACTS[name] ?? name
  let data: unknown = null
  try {
    data = JSON.parse(content)
  } catch {
    /* empty */
  }
  if (!data || typeof data !== 'object') {
    return {
      name,
      title,
      summary: { approved: 0, rejected: 0, warnings: [] },
      decisions: [],
      raw: data,
    }
  }
  const obj = data as Record<string, unknown>

  if (name === 'key_info_review_report.json') {
    return {
      name,
      title,
      summary: normalizeSummary(obj),
      decisions: parseDecisionList(obj.decisions, 'key_info_id'),
      raw: obj,
    }
  }

  if (name === 'possible_errors_review_report.json') {
    return {
      name,
      title,
      summary: normalizeSummary(obj),
      decisions: parseDecisionList(obj.decisions, 'error_id'),
      raw: obj,
    }
  }

  const { summary, decisions } = normalizeContentReview(obj)
  return { name, title, summary, decisions, raw: obj }
}
