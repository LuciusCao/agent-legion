import { useEffect, useState } from 'react'
import { fetchJobArtifact } from '../api'
import type { QuestionArtifactNormalized } from '../types'

export interface UseJobQuestionReturn {
  question: QuestionArtifactNormalized | null
  loading: boolean
  error: string
}

interface QuestionsArtifact {
  questions?: unknown[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function extractFirstQuestion(
  data: QuestionsArtifact
): QuestionArtifactNormalized | null {
  if (!Array.isArray(data.questions) || data.questions.length === 0) {
    return null
  }
  const first = data.questions[0]
  if (!isRecord(first)) return null
  const normalized = first.normalized
  if (!isRecord(normalized)) return null
  return normalized as QuestionArtifactNormalized
}

export function useJobQuestion(
  jobId: string,
  refreshKey = ''
): UseJobQuestionReturn {
  const [question, setQuestion] = useState<QuestionArtifactNormalized | null>(
    null
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    fetchJobArtifact(jobId, 'questions.json')
      .then((artifact) => {
        if (cancelled) return
        const data = JSON.parse(artifact.content) as QuestionsArtifact
        setQuestion(extractFirstQuestion(data))
        setError('')
      })
      .catch((err) => {
        if (cancelled) return
        setQuestion(null)
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [jobId, refreshKey])

  return { question, loading, error }
}
