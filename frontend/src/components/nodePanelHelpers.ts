export interface ReviewEntry {
  item_id: string
  status: string
  issues?: Array<{ title?: string; details?: string }>
}

export interface ReviewResult {
  status?: string
  reviews?: ReviewEntry[]
}

export function getReviewMap(review: unknown): Map<string, ReviewEntry> {
  const map = new Map<string, ReviewEntry>()
  if (!review || typeof review !== 'object') return map
  const r = review as ReviewResult
  if (Array.isArray(r.reviews)) {
    for (const entry of r.reviews) {
      if (entry.item_id) {
        map.set(entry.item_id, entry)
      }
    }
  }
  return map
}

export function getGlobalStatus(review: unknown): string | undefined {
  if (!review || typeof review !== 'object') return undefined
  return (review as ReviewResult).status
}

export function formatIssue(issue: {
  title?: string
  details?: string
}): string {
  if (issue.title && issue.details) return `${issue.title}：${issue.details}`
  return issue.details || issue.title || ''
}

export const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  published: { text: '已通过', color: '#2e7d32' },
  pending_review: { text: '待审', color: '#ed6c02' },
  rejected: { text: '驳回', color: '#ba1a1a' },
}
