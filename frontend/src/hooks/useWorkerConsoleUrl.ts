import { useQuery } from '@tanstack/react-query'
import { fetchAgentWorkers } from '../api/agentWorkers'
import { extraQueryKeys } from '../lib/queryKeysExtra'

/**
 * 主控制台里「打开 Worker 控制台」入口的地址：部署级兜底值
 * （AGENT_LEGION_WORKER_CONSOLE_URL），随 GET /api/agent-workers 的
 * console_url 下发，通常指向部署机本地 Worker 的控制台。空串 = 未配置，
 * 调用方退化为纯文字说明；undefined = 尚未加载完（调用方先不渲染入口，
 * 也不闪「未配置」提示）。地址是部署拓扑、几乎不变：长 staleTime，
 * 设置页 / Worker 列表空态 / 顶栏弹层多个入口共享一次请求。
 */
export function useWorkerConsoleUrl(): string | undefined {
  const { data, isError } = useQuery({
    queryKey: extraQueryKeys.workerConsole(),
    queryFn: () => fetchAgentWorkers(),
    staleTime: 5 * 60_000,
  })
  if (isError) return ''
  if (data === undefined) return undefined
  return data.console_url ?? ''
}
