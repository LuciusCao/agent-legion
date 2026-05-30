import { INTERACTION_REVIEW_STATUS_LABELS } from '../labels'

export function InteractionReviewBadge({ status }: { status?: string }) {
  if (!status) return null
  return (
    <span className={`review-badge ${status.replace(/_/g, '-')}`}>
      {INTERACTION_REVIEW_STATUS_LABELS[status]}
    </span>
  )
}
