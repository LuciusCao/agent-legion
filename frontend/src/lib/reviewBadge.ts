import type { ReviewDecision } from './reviewReport'

export const DECISION_BADGE: Record<
  ReviewDecision['decision'],
  {
    className:
      | 'approvedBadge'
      | 'rejectedBadge'
      | 'attentionBadge'
      | 'unknownBadge'
    icon: string
  }
> = {
  approved: { className: 'approvedBadge', icon: 'check_circle' },
  rejected: { className: 'rejectedBadge', icon: 'close' },
  needs_revision: { className: 'attentionBadge', icon: 'build_circle' },
  unknown: { className: 'unknownBadge', icon: 'help' },
}
