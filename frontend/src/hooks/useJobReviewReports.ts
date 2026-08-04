import { useEffect, useRef, useState } from 'react'
import { fetchJobArtifact } from '../api'
import { parseReviewReport, type ReviewReport } from '../lib/reviewReport'

const REPORTS = {
  keyInfo: 'key_info_review_report.json',
  possibleErrors: 'possible_errors_review_report.json',
}

export type ReviewReportMap = Record<string, ReviewReport>

export interface UseJobReviewReportsReturn {
  reports: ReviewReportMap
  loading: boolean
  error: string
}

export function useJobReviewReports(
  jobId: string,
  keyInfoReviewAttempted = false,
  possibleErrorsReviewAttempted = false,
  refreshKey = ''
): UseJobReviewReportsReturn {
  const [reports, setReports] = useState<ReviewReportMap>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const requestIdRef = useRef(0)

  useEffect(() => {
    const toFetch: string[] = []
    if (keyInfoReviewAttempted) toFetch.push(REPORTS.keyInfo)
    if (possibleErrorsReviewAttempted) toFetch.push(REPORTS.possibleErrors)
    if (toFetch.length === 0) {
      queueMicrotask(() => setReports({}))
      return
    }
    const requestId = ++requestIdRef.current
    queueMicrotask(() => {
      if (requestId === requestIdRef.current) setLoading(true)
    })
    void Promise.all(
      toFetch.map(async (name) => {
        try {
          const { content } = await fetchJobArtifact(jobId, name)
          if (requestId !== requestIdRef.current) return
          setReports((prev) => ({
            ...prev,
            [name]: parseReviewReport(name, content),
          }))
          setError('')
        } catch (err) {
          if (requestId !== requestIdRef.current) return
          setError(err instanceof Error ? err.message : String(err))
        }
      })
    ).finally(() => {
      if (requestId === requestIdRef.current) setLoading(false)
    })
  }, [jobId, keyInfoReviewAttempted, possibleErrorsReviewAttempted, refreshKey])

  return { reports, loading, error }
}
