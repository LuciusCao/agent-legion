import { useQuery } from '@tanstack/react-query'
import { fetchJobArtifactJson } from './jobArtifactJson'
import { useJobDetailQuery } from './useJobDetailQuery'
import { comprehensionVersion } from '../lib/jobArtifactVersions'
import { queryKeys } from '../lib/queryKeys'
import { toErrorMessage } from '../lib/queryError'
import {
  buildComprehensionInfo,
  extractComprehensionInfo,
  type KeyInfoArtifact,
  type PossibleErrorsArtifact,
} from './comprehensionInfoExtraction'
import type { ComprehensionInfo } from '../types'

export interface UseJobComprehensionInfoReturn {
  info: ComprehensionInfo | null
  loading: boolean
  error: string
}

// 串行 fallback 链保持原样：reviewed → raw → null。
async function fetchReviewedOrRaw<T>(
  jobId: string,
  reviewedName: string,
  rawName: string
): Promise<T | null> {
  try {
    return await fetchJobArtifactJson<T>(jobId, reviewedName)
  } catch {
    try {
      return await fetchJobArtifactJson<T>(jobId, rawName)
    } catch {
      return null
    }
  }
}

async function loadComprehensionInfo(
  jobId: string
): Promise<ComprehensionInfo | null> {
  try {
    const data = await fetchJobArtifactJson<Record<string, unknown>>(
      jobId,
      'comprehension_info.json'
    )
    const extracted = extractComprehensionInfo(data)
    if (extracted) return extracted
  } catch {
    // Fall back to intermediate artifacts below.
  }
  const keyInfoArtifact = await fetchReviewedOrRaw<KeyInfoArtifact>(
    jobId,
    'key_info_reviewed.json',
    'key_info_raw.json'
  )
  const possibleErrorsArtifact =
    await fetchReviewedOrRaw<PossibleErrorsArtifact>(
      jobId,
      'possible_errors_reviewed.json',
      'possible_errors_raw.json'
    )
  return buildComprehensionInfo(keyInfoArtifact, possibleErrorsArtifact)
}

export function useJobComprehensionInfo(
  jobId: string
): UseJobComprehensionInfoReturn {
  const { data: detail } = useJobDetailQuery(jobId)
  const query = useQuery({
    queryKey: queryKeys.jobArtifact(
      jobId,
      'comprehension_info.json',
      comprehensionVersion(detail ?? null)
    ),
    queryFn: () => loadComprehensionInfo(jobId),
  })
  return {
    info: query.data ?? null,
    loading: query.isPending,
    error: toErrorMessage(query.error),
  }
}
