import { useAsync } from './useAsync'
import { fetchJobArtifactJson } from './jobArtifactJson'
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

async function fetchKeyInfoArtifact(
  jobId: string
): Promise<KeyInfoArtifact | null> {
  try {
    return await fetchJobArtifactJson<KeyInfoArtifact>(
      jobId,
      'key_info_reviewed.json'
    )
  } catch {
    try {
      return await fetchJobArtifactJson<KeyInfoArtifact>(
        jobId,
        'key_info_raw.json'
      )
    } catch {
      return null
    }
  }
}

async function fetchPossibleErrorsArtifact(
  jobId: string
): Promise<PossibleErrorsArtifact | null> {
  try {
    return await fetchJobArtifactJson<PossibleErrorsArtifact>(
      jobId,
      'possible_errors_reviewed.json'
    )
  } catch {
    try {
      return await fetchJobArtifactJson<PossibleErrorsArtifact>(
        jobId,
        'possible_errors_raw.json'
      )
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
  const keyInfoArtifact = await fetchKeyInfoArtifact(jobId)
  const possibleErrorsArtifact = await fetchPossibleErrorsArtifact(jobId)
  return buildComprehensionInfo(keyInfoArtifact, possibleErrorsArtifact)
}

export function useJobComprehensionInfo(
  jobId: string,
  refreshKey = ''
): UseJobComprehensionInfoReturn {
  const {
    data: info,
    loading,
    error,
  } = useAsync(() => loadComprehensionInfo(jobId), [jobId, refreshKey])

  return { info, loading, error }
}
