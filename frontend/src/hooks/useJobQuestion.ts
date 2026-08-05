import { useQuery } from '@tanstack/react-query'
import { fetchJobArtifactJson } from './jobArtifactJson'
import { useJobDetailQuery } from './useJobDetailQuery'
import { questionArtifactVersion } from '../lib/jobArtifactVersions'
import { queryKeys } from '../lib/queryKeys'
import { toErrorMessage } from '../lib/queryError'
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

export function useJobQuestion(jobId: string): UseJobQuestionReturn {
  // 订阅共享 detail 查询；产出节点状态作为版本编进 queryKey，版本变即重取。
  const { data: detail } = useJobDetailQuery(jobId)
  const query = useQuery({
    queryKey: queryKeys.jobArtifact(
      jobId,
      'questions.json',
      questionArtifactVersion(detail ?? null)
    ),
    queryFn: async () =>
      extractFirstQuestion(
        await fetchJobArtifactJson<QuestionsArtifact>(jobId, 'questions.json')
      ),
  })
  return {
    question: query.data ?? null,
    loading: query.isPending,
    error: toErrorMessage(query.error),
  }
}
