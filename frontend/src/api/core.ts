import { httpError } from './httpError'
import { handleUnauthorized, withCsrfHeader } from './requestAuth'
import {
  linkAbort,
  resolveTimeout,
  timeoutError,
  type ApiInit,
} from './requestTimeout'

export async function api<T>(path: string, init?: ApiInit): Promise<T> {
  const { timeoutMs, signal, ...requestInit } = init ?? {}
  const method = (requestInit.method ?? 'GET').toUpperCase()
  const headers = withCsrfHeader(method, {
    'Content-Type': 'application/json',
    ...(requestInit.headers ?? {}),
  } as Record<string, string>)
  const timeout = resolveTimeout(method, timeoutMs)
  const abort = linkAbort(signal, timeout)
  try {
    const response = await fetch(path, {
      ...(method === 'GET' ? { cache: 'no-store' } : {}),
      ...requestInit,
      headers,
      signal: abort.signal,
    })
    if (response.status === 401) handleUnauthorized(path)
    if (!response.ok) throw await httpError(response)
    return (await response.json()) as T
  } catch (err) {
    if (abort.timedOut() && timeout !== null) throw timeoutError(timeout)
    throw err
  } finally {
    abort.dispose()
  }
}
