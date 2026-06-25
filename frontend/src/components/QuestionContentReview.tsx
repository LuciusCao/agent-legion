import { useMemo } from 'react'
import { MaterialIcon } from './MaterialIcon'
import styles from './QuestionContentReview.module.css'
import type { ReviewDecision, ReviewReport } from '../lib/reviewReport'

export type ReviewDecisionMap = Map<string, ReviewDecision>

export function buildDecisionMap(
  decisions: ReviewDecision[]
): ReviewDecisionMap {
  return new Map(decisions.map((d) => [d.id, d]))
}

const DECISION_META: Record<
  ReviewDecision['decision'],
  { icon: string; label: string; className: string }
> = {
  approved: {
    icon: 'check_circle',
    label: '通过',
    className: styles.reviewApproved,
  },
  rejected: {
    icon: 'close',
    label: '拒绝',
    className: styles.reviewRejected,
  },
  needs_revision: {
    icon: 'build_circle',
    label: '需修改',
    className: styles.reviewAttention,
  },
  unknown: {
    icon: 'help',
    label: '未知',
    className: styles.reviewUnknown,
  },
}

export function useReviewDecisionMaps(
  reports: Record<string, ReviewReport | undefined>
) {
  const keyInfoDecisions = useMemo(() => {
    const report = reports['key_info_review_report.json']
    return report
      ? buildDecisionMap(report.decisions)
      : new Map<string, ReviewDecision>()
  }, [reports])

  const possibleErrorDecisions = useMemo(() => {
    const report = reports['possible_errors_review_report.json']
    return report
      ? buildDecisionMap(report.decisions)
      : new Map<string, ReviewDecision>()
  }, [reports])

  return { keyInfoDecisions, possibleErrorDecisions }
}

export function ReviewChipStatus({ decision }: { decision: ReviewDecision }) {
  const meta = DECISION_META[decision.decision]
  return (
    <MaterialIcon
      name={meta.icon}
      className={`${styles.reviewIcon} ${meta.className}`}
    />
  )
}

export function ReviewDetailStatus({ decision }: { decision: ReviewDecision }) {
  const meta = DECISION_META[decision.decision]
  return (
    <div className={styles.reviewSection}>
      <div className={styles.reviewDecision}>
        <MaterialIcon
          name={meta.icon}
          className={`${styles.reviewIcon} ${meta.className}`}
        />
        <span>审核结果：{meta.label}</span>
      </div>
      {decision.reason && (
        <div className={styles.reviewReason}>{decision.reason}</div>
      )}
    </div>
  )
}
