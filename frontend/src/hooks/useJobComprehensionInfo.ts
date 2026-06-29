import { useEffect, useState } from 'react'
import { fetchJobArtifact } from '../api'
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
        const data = JSON.parse(artifact.content)
        const extracted = extractComprehensionInfo(data)
        if (extracted) {
          setInfo(extracted)
          setError('')
          setLoading(false)
          return
        }
        return loadFromIntermediateArtifacts()
      })
      .catch(() => {
        if (cancelled) return
        return loadFromIntermediateArtifacts()
      })

    async function loadFromIntermediateArtifacts() {
      const keyInfoArtifact = await fetchKeyInfoArtifact()
      const possibleErrorsArtifact = await fetchPossibleErrorsArtifact()
      if (cancelled) return
      const extracted = buildComprehensionInfo(
        keyInfoArtifact,
        possibleErrorsArtifact
      )
      setInfo(extracted)
      setError('')
      setLoading(false)
    }

    async function fetchKeyInfoArtifact(): Promise<KeyInfoArtifact | null> {
      try {
        const artifact = await fetchJobArtifact(jobId, 'key_info_reviewed.json')
        return JSON.parse(artifact.content) as KeyInfoArtifact
      } catch {
        try {
          const artifact = await fetchJobArtifact(jobId, 'key_info_raw.json')
          return JSON.parse(artifact.content) as KeyInfoArtifact
        } catch {
          return null
        }
      }
    }

    async function fetchPossibleErrorsArtifact(): Promise<PossibleErrorsArtifact | null> {
      try {
        const artifact = await fetchJobArtifact(
          jobId,
          'possible_errors_reviewed.json'
        )
        return JSON.parse(artifact.content) as PossibleErrorsArtifact
      } catch {
        try {
          const artifact = await fetchJobArtifact(
            jobId,
            'possible_errors_raw.json'
          )
          return JSON.parse(artifact.content) as PossibleErrorsArtifact
        } catch {
          return null
        }
      }
    }

    return () => {
      cancelled = true
    }
  }, [jobId, refreshKey])

  return { info, loading, error }
}
