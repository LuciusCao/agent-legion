/**
 * #719：`api()` 的超时与取消机制——默认超时（读/写分档、可按调用覆写或
 * 关闭）、调用方 AbortSignal 与超时计时器的合并、结构化超时错误。
 */
/** #719：读请求（GET/HEAD）默认超时——后端僵死时查询失败而非无限悬挂，
 * react-query 的重试与 refetchInterval 才能继续工作。 */
export const DEFAULT_READ_TIMEOUT_MS = 30_000
/** #719：写请求默认超时放宽：批量重跑/升级/发布等写操作服务端可能合法地
 * 耗时更久，且客户端中止并不撤销服务端动作，过早超时只会误报失败。 */
export const DEFAULT_WRITE_TIMEOUT_MS = 120_000
/** #719：批量操作（batch-rerun/run-to/delete/package/clear-packed/升级/
 * 暂停恢复）服务端同步处理整批（可达全量筛选结果），耗时随规模增长，
 * 豁免超时。 */
export const BULK_REQUEST_TIMEOUT = null
/** 超时错误的结构化错误码（错误映射层据此出文案，与 #718 联动）。 */
export const REQUEST_TIMEOUT_CODE = 'request_timeout'

export interface ApiInit extends RequestInit {
  /** 覆写默认超时（毫秒）；`null` 关闭超时（长耗时端点豁免用）。 */
  timeoutMs?: number | null
}

export type ApiTimeoutError = Error & {
  code: typeof REQUEST_TIMEOUT_CODE
  timeoutMs: number
}

export function isRequestTimeoutError(
  error: unknown
): error is ApiTimeoutError {
  return (
    error instanceof Error &&
    (error as { code?: unknown }).code === REQUEST_TIMEOUT_CODE
  )
}

export function requestTimeoutMessage(timeoutMs: number): string {
  return `请求超时：服务端 ${Math.round(timeoutMs / 1000)} 秒未响应，请稍后重试`
}

export function timeoutError(timeoutMs: number): ApiTimeoutError {
  return Object.assign(new Error(requestTimeoutMessage(timeoutMs)), {
    code: REQUEST_TIMEOUT_CODE,
    timeoutMs,
  } as const)
}

export function resolveTimeout(
  method: string,
  timeoutMs: number | null | undefined
) {
  if (timeoutMs === null) return null
  if (typeof timeoutMs === 'number') return timeoutMs > 0 ? timeoutMs : null
  return method === 'GET' || method === 'HEAD'
    ? DEFAULT_READ_TIMEOUT_MS
    : DEFAULT_WRITE_TIMEOUT_MS
}

/**
 * 合并调用方 signal（react-query 卸载/失效取消）与超时计时器为一个
 * AbortController。调用方取消原样以 AbortError 抛出（RQ 视为取消而非失败）；
 * 只有计时器触发才转换成超时错误。计时器覆盖到响应体读完为止。
 */
export function linkAbort(
  signal: AbortSignal | null | undefined,
  timeoutMs: number | null
) {
  const controller = new AbortController()
  let timedOut = false
  const onAbort = () => controller.abort(signal?.reason)
  if (signal?.aborted) controller.abort(signal.reason)
  else signal?.addEventListener('abort', onAbort, { once: true })
  const timer =
    timeoutMs === null
      ? null
      : setTimeout(() => {
          timedOut = true
          controller.abort()
        }, timeoutMs)
  return {
    signal: controller.signal,
    timedOut: () => timedOut,
    dispose: () => {
      if (timer) clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    },
  }
}
