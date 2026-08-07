import { api } from './core'
import type {
  ExecutorCreateRequest,
  ExecutorDetailResponse,
  ExecutorListResponse,
  ExecutorPayload,
  ExecutorVersion,
  ExecutorVersionsResponse,
} from '../types'

const base = '/api/executor-definitions'

export async function fetchExecutorDefinitions(): Promise<ExecutorListResponse> {
  return api(base)
}

export async function fetchExecutorDefinition(
  executorId: string
): Promise<ExecutorDetailResponse> {
  return api(`${base}/${encodeURIComponent(executorId)}`)
}

export async function fetchExecutorVersions(
  executorId: string
): Promise<ExecutorVersionsResponse> {
  return api(`${base}/${encodeURIComponent(executorId)}/versions`)
}

export async function createExecutorDefinition(
  payload: ExecutorCreateRequest
): Promise<ExecutorVersion> {
  return api(base, { method: 'POST', body: JSON.stringify(payload) })
}

export async function saveExecutorDraft(
  executorId: string,
  payload: ExecutorPayload
): Promise<ExecutorVersion> {
  return api(`${base}/${encodeURIComponent(executorId)}/draft`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function publishExecutor(
  executorId: string
): Promise<ExecutorVersion> {
  return api(`${base}/${encodeURIComponent(executorId)}/publish`, {
    method: 'POST',
  })
}

export async function rollbackExecutor(
  executorId: string,
  version: number
): Promise<ExecutorVersion> {
  return api(`${base}/${encodeURIComponent(executorId)}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ version }),
  })
}

export async function copyExecutor(
  executorId: string,
  newExecutorId: string
): Promise<ExecutorVersion> {
  return api(`${base}/${encodeURIComponent(executorId)}/copy`, {
    method: 'POST',
    body: JSON.stringify({ new_executor_id: newExecutorId }),
  })
}

export async function archiveExecutor(
  executorId: string
): Promise<{ archived: number }> {
  return api(`${base}/${encodeURIComponent(executorId)}`, { method: 'DELETE' })
}
