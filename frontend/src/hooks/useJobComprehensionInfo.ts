import { useEffect, useState } from 'react'
import { fetchJobArtifact } from '../api'
import type { ComprehensionInfo } from '../types'

export interface UseJobComprehensionInfoReturn {
  info: ComprehensionInfo | null
  loading: boolean
  error: string
}

interface ComprehensionArtifact {
  question_id?: unknown
  fingerprint?: unknown
  fingerprint_source?: unknown
  fingerprint_missing?: unknown
  comprehension_data?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function extractComprehensionInfo(
  data: ComprehensionArtifact
): ComprehensionInfo | null {
  if (!isRecord(data)) return null
  const comprehensionData = data.comprehension_data
  if (!isRecord(comprehensionData)) return null
  const keyInfoList = comprehensionData.key_info_list
  if (!Array.isArray(keyInfoList) || keyInfoList.length === 0) return null
  return data as ComprehensionInfo
}

export function useJobComprehensionInfo(
  jobId: string,
  refreshKey = ''
): UseJobComprehensionInfoReturn {
  const [info, setInfo] = useState<ComprehensionInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    fetchJobArtifact(jobId, 'comprehension_info.json')
      .then((artifact) => {
        if (cancelled) return
        const data = JSON.parse(artifact.content) as ComprehensionArtifact
        setInfo(extractComprehensionInfo(data))
        setError('')
      })
      .catch((err) => {
        if (cancelled) return
        setInfo(null)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [jobId, refreshKey])

  return { info, loading, error }
}
