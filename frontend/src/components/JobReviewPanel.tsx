import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchJobArtifact } from '../api'
import {
  isReviewArtifact,
  parseReviewReport,
  type ReviewReport,
} from '../lib/reviewReport'
import { MaterialIcon } from './MaterialIcon'
import styles from './JobReviewPanel.module.css'

type ReportState =
  | { status: 'loading' }
  | { status: 'done'; report: ReviewReport }
  | { status: 'error'; message: string }

export interface JobReviewPanelProps {
  jobId: string
  artifacts: string[]
  refreshKey?: string
}

export function JobReviewPanel({
  jobId,
  artifacts,
  refreshKey,
}: JobReviewPanelProps) {
  const reviewArtifactNames = useMemo(
    () => artifacts.filter(isReviewArtifact),
    [artifacts]
  )
  const [reports, setReports] = useState<Record<string, ReportState>>({})
  const requestIdRef = useRef(0)

  useEffect(() => {
    if (reviewArtifactNames.length === 0) return
    const requestId = ++requestIdRef.current

    void Promise.all(
      reviewArtifactNames.map(async (name) => {
        try {
          const { content } = await fetchJobArtifact(jobId, name)
          if (requestId !== requestIdRef.current) return
          setReports((prev) => ({
            ...prev,
            [name]: {
              status: 'done',
              report: parseReviewReport(name, content),
            },
          }))
        } catch (err) {
          if (requestId !== requestIdRef.current) return
          const message = err instanceof Error ? err.message : String(err)
          setReports((prev) => ({
            ...prev,
            [name]: { status: 'error', message },
          }))
        }
      })
    )
  }, [jobId, reviewArtifactNames, refreshKey])

  if (reviewArtifactNames.length === 0) return null

  return (
    <div className={styles.panel}>
      <h2 className={styles.title}>Agent 审核结果</h2>
      {reviewArtifactNames.map((name) => {
        const state = reports[name]
        if (!state || state.status === 'loading') {
          return (
            <div key={name} className={styles.loading}>
              加载 {name}...
            </div>
          )
        }
        if (state.status === 'error') {
          return (
            <div key={name} className={styles.error}>
              {state.message}
            </div>
          )
        }
        const report = state.report
        return (
          <section className={styles.card} key={name}>
            <div className={styles.header}>
              <h3 className={styles.cardTitle}>{report.title}</h3>
              <div className={styles.summary}>
                <span className={styles.approved} title="通过">
                  <MaterialIcon name="check_circle" className={styles.icon} />
                  {report.summary.approved}
                </span>
                <span className={styles.rejected} title="拒绝">
                  <MaterialIcon name="close" className={styles.icon} />
                  {report.summary.rejected}
                </span>
              </div>
            </div>
            {report.summary.warnings.length > 0 && (
              <ul className={styles.warnings}>
                {report.summary.warnings.map((warning, idx) => (
                  <li key={idx}>{warning}</li>
                ))}
              </ul>
            )}
            {report.decisions.length > 0 && (
              <ul className={styles.decisions}>
                {report.decisions.map((decision) => (
                  <li key={decision.id} className={styles.decision}>
                    <span
                      className={
                        styles[
                          decision.decision === 'approved'
                            ? 'approvedBadge'
                            : decision.decision === 'rejected'
                              ? 'rejectedBadge'
                              : 'unknownBadge'
                        ]
                      }
                    >
                      <MaterialIcon
                        name={
                          decision.decision === 'approved'
                            ? 'check_circle'
                            : decision.decision === 'rejected'
                              ? 'close'
                              : 'help'
                        }
                        className={styles.icon}
                      />
                    </span>
                    <span className={styles.decisionId}>{decision.id}</span>
                    {decision.reason && (
                      <span className={styles.reason}>{decision.reason}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )
      })}
    </div>
  )
}
