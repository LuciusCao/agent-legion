import { QueryClient } from '@tanstack/react-query'

// api/core.ts 抛出的 Error 通过 `status` 字段携带 HTTP 状态码；无 status 的是
// 网络层错误。仅 5xx 与网络错误重试（最多 2 次）；4xx 不重试——401 已由
// core.ts 的 handleUnauthorized 走跳登录链路，RQ 层不重复处理。
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false
  const status = (error as { status?: unknown } | null)?.status
  if (typeof status !== 'number') return true
  return status >= 500
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      retry: shouldRetry,
    },
  },
})
