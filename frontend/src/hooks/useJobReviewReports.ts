import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchJobArtifact } from '../api'
import { isReviewArtifact, parseReviewReport } from '../lib/reviewReport'
import type { ReviewReport } from '../lib/reviewReport'

export type ReviewReportMap = Record<string, ReviewReport>

export interface UseJobReviewReportsReturn {
  reports: ReviewReportMap
  loading: boolean
  error: string
}

export function useJobReviewReports(
  jobId: string,
  artifactNames: string[],
  refreshKey = ''
): UseJobReviewReportsReturn {
  const reviewNames = useMemo(
    () => artifactNames.filter(isReviewArtifact),
    [artifactNames]
  )
  const [reports, setReports] = useState<ReviewReportMap>({})
  const [error, setError] = useState('')
  const requestIdRef = useRef(0)

  useEffect(() => {
    if (reviewNames.length === 0) return

    const requestId = ++requestIdRef.current

    void Promise.all(
      reviewNames.map(async (name) => {
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
    )
  }, [jobId, reviewNames, refreshKey])

  return { reports, loading: false, error }
}
