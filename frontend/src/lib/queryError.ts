import {
  isRequestTimeoutError,
  requestTimeoutMessage,
} from '../api/requestTimeout'

/** 把 useQuery/useQueries 的 error 映射为展示用字符串（无错误时为空串）。
 * #719：结构化错误码优先（超时 → 统一文案），其余沿用 message。 */
export function toErrorMessage(error: unknown): string {
  if (!error) return ''
  if (isRequestTimeoutError(error)) {
    return requestTimeoutMessage(error.timeoutMs)
  }
  return error instanceof Error ? error.message : String(error)
}
