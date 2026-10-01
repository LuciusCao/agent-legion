import type { AgentWorkerSummary } from '../api/agentWorkers'

/**
 * Worker 注册时自报或由操作者配置的控制台地址：labels 的可选键 console_url
 * （worker/console_url.py）。旧版 Worker 没有这个键 → 空串，不显示入口。
 * 只接受 http(s) 地址，避免把任意标签值渲染成链接。
 */
export const WORKER_CONSOLE_LABEL = 'console_url'

export function workerConsoleUrl(
  worker: Pick<AgentWorkerSummary, 'labels'>
): string {
  const value = worker.labels?.[WORKER_CONSOLE_LABEL]
  return typeof value === 'string' && /^https?:\/\//i.test(value.trim())
    ? value.trim()
    : ''
}

/** 修复领取阻塞时只选有效在线 Worker，避免被旧机器的地址抢占入口。 */
export function readyWorkerConsoleUrl(
  workers: AgentWorkerSummary[],
  fallback: string
): string {
  return (
    workers
      .filter((worker) => worker.online && !worker.revoked)
      .map(workerConsoleUrl)
      .find(Boolean) ?? fallback
  )
}
