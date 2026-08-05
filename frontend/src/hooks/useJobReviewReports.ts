import { useQueries } from '@tanstack/react-query'
import { fetchJobArtifact } from '../api'
import { parseReviewReport, type ReviewReport } from '../lib/reviewReport'
import { comprehensionVersion } from '../lib/jobArtifactVersions'
import { toErrorMessage } from '../lib/queryError'
import { queryKeys } from '../lib/queryKeys'
import { useJobDetailQuery } from './useJobDetailQuery'

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

async function fetchReviewReport(
  jobId: string,
  name: string
): Promise<ReviewReport> {
  const { content } = await fetchJobArtifact(jobId, name)
  return parseReviewReport(name, content)
}

export function useJobReviewReports(
  jobId: string,
  keyInfoReviewAttempted = false,
  possibleErrorsReviewAttempted = false
): UseJobReviewReportsReturn {
  const { data: detail } = useJobDetailQuery(jobId)
  const version = comprehensionVersion(detail ?? null)
  // 每个 report 文件一条独立 query：成功进 reports、失败进 error，
  // 保留逐文件部分成功语义（不得一损俱损）。
  const results = useQueries({
    queries: [
      {
        queryKey: queryKeys.jobArtifact(jobId, REPORTS.keyInfo, version),
        queryFn: () => fetchReviewReport(jobId, REPORTS.keyInfo),
        enabled: keyInfoReviewAttempted,
      },
      {
        queryKey: queryKeys.jobArtifact(jobId, REPORTS.possibleErrors, version),
        queryFn: () => fetchReviewReport(jobId, REPORTS.possibleErrors),
        enabled: possibleErrorsReviewAttempted,
      },
    ],
  })

  const names = [REPORTS.keyInfo, REPORTS.possibleErrors]
  const reports: ReviewReportMap = {}
  let error = ''
  results.forEach((result, index) => {
    if (result.data) reports[names[index]] = result.data
    if (!error) error = toErrorMessage(result.error)
  })
  const loading = results.some((result) => result.isLoading)

  return { reports, loading, error }
}
