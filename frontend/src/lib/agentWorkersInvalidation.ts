import type { QueryClient } from '@tanstack/react-query'
import { queryKeys } from './queryKeys'

// 与原 executorsStore.refreshWorkers 同档的 750ms 防抖：合并 SSE 事件风暴，
// 避免每个事件都触发一次 workers refetch。
const INVALIDATE_DEBOUNCE_MS = 750

let timer: ReturnType<typeof setTimeout> | null = null

/** 防抖失效 agentWorkers 查询；仅当有活跃观察者（如 Worker 状态列表）时才触发 refetch。 */
export function invalidateAgentWorkers(queryClient: QueryClient): void {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    void queryClient.invalidateQueries({ queryKey: queryKeys.agentWorkers() })
  }, INVALIDATE_DEBOUNCE_MS)
}
