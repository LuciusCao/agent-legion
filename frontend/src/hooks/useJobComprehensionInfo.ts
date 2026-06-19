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

interface KeyInfoReviewedArtifact {
  question_id?: unknown
  key_info_list?: unknown
}

interface PossibleErrorsReviewedArtifact {
  question_id?: unknown
  possible_error_list?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function hasKeyInfoList(
  value: unknown
): value is { question_id?: unknown; key_info_list: unknown[] } {
  return isRecord(value) && Array.isArray(value.key_info_list)
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

function extractComprehensionInfoFromIntermediate(
  keyInfoData: KeyInfoReviewedArtifact,
  possibleErrorsData: PossibleErrorsReviewedArtifact
): ComprehensionInfo | null {
  if (!hasKeyInfoList(keyInfoData)) return null
  const keyInfoList = keyInfoData.key_info_list
  if (keyInfoList.length === 0) return null
  const possibleErrorList = isRecord(possibleErrorsData)
    ? possibleErrorsData.possible_error_list
    : undefined
  if (!Array.isArray(possibleErrorList)) return null
  return {
    question_id: String(
      keyInfoData.question_id ?? possibleErrorsData.question_id ?? ''
    ),
    fingerprint: null,
    fingerprint_source: 'missing',
    fingerprint_missing: true,
    comprehension_data: {
      key_info_list: keyInfoList,
      possible_error_list: possibleErrorList,
    },
  } as ComprehensionInfo
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
        const extracted = extractComprehensionInfo(data)
        if (extracted) {
          setInfo(extracted)
          setError('')
          setLoading(false)
          return
        }
        // Final artifact exists but has no key info; fall back to intermediate artifacts.
        return loadFromIntermediateArtifacts()
      })
      .catch(() => {
        if (cancelled) return
        return loadFromIntermediateArtifacts()
      })

    function loadFromIntermediateArtifacts() {
      return Promise.all([
        fetchJobArtifact(jobId, 'key_info_reviewed.json').catch(() => null),
        fetchJobArtifact(jobId, 'possible_errors_reviewed.json').catch(
          () => null
        ),
      ])
        .then(([keyInfoArtifact, possibleErrorsArtifact]) => {
          if (cancelled) return
          if (!keyInfoArtifact || !possibleErrorsArtifact) {
            setInfo(null)
            setError('')
            setLoading(false)
            return
          }
          const keyInfoData = JSON.parse(
            keyInfoArtifact.content
          ) as KeyInfoReviewedArtifact
          const possibleErrorsData = JSON.parse(
            possibleErrorsArtifact.content
          ) as PossibleErrorsReviewedArtifact
          const extracted = extractComprehensionInfoFromIntermediate(
            keyInfoData,
            possibleErrorsData
          )
          setInfo(extracted)
          setError('')
          setLoading(false)
        })
        .catch((err) => {
          if (cancelled) return
          setInfo(null)
          setError(err instanceof Error ? err.message : String(err))
          setLoading(false)
        })
    }

    return () => {
      cancelled = true
    }
  }, [jobId, refreshKey])

  return { info, loading, error }
}
