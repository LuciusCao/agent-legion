import { useQuery } from '@tanstack/react-query'
import { fetchWorkerConsole } from '../api/agentWorkers'
import { extraQueryKeys } from '../lib/queryKeysExtra'

/**
 * 主控制台里「打开 Worker 控制台」入口的地址：部署级兜底值
 * （AGENT_LEGION_WORKER_CONSOLE_URL）。专用接口只返回部署元数据，
 * 不读取或缓存任何 workspace 的 Worker 清单。空串只表示明确未配置；
 * 首次请求失败是未知，后台刷新失败仍保留已有配置快照。
 */
export function useWorkerConsoleConfig(enabled = true) {
  return useQuery({
    queryKey: extraQueryKeys.workerConsole(),
    queryFn: fetchWorkerConsole,
    staleTime: 5 * 60_000,
    enabled,
  })
}

export function useWorkerConsoleUrl(enabled = true): string | undefined {
  return useWorkerConsoleConfig(enabled).data?.console_url
}
