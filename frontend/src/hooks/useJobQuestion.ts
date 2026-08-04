import { useAsync } from './useAsync'
import { fetchJobArtifactJson } from './jobArtifactJson'
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
  const {
    data: question,
    loading,
    error,
  } = useAsync(async () => {
    const data = await fetchJobArtifactJson<QuestionsArtifact>(
      jobId,
      'questions.json'
    )
    return extractFirstQuestion(data)
  }, [jobId, refreshKey])

  return { question, loading, error }
}
