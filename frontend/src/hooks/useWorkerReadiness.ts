import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listAgentWorkers } from '../api'
import type { AgentWorkerSummary } from '../api'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { useAgentsStore } from '../stores/agentsStore'
import { useWorkerConsoleUrl } from './useWorkerConsoleUrl'

export interface WorkerReadiness {
  /** 本 workspace 视角的 Worker 列表；undefined = 首次加载中。 */
  workers: AgentWorkerSummary[] | undefined
  /** workspace 调度是否暂停（顶栏「已暂停／运行中」）。 */
  paused: boolean
  /** 部署级 Worker 控制台地址（空串 = 未配置）。 */
  consoleUrl: string
  resumeScheduling: () => void
}

/**
 * 「任务能不能跑起来」的三个信号：Worker 列表（5s 轮询，与设置页同 key
 * 共享缓存）、调度暂停位、Worker 控制台入口地址。新 workspace 引导的
 * 两步与任务列表的「等待中」排查横幅共用。
 */
export function useWorkerReadiness(
  workspaceId: string | undefined,
  enabled = true
): WorkerReadiness {
  const paused = useAgentsStore((s) => s.getWorkerPaused(workspaceId ?? ''))
  const setWorkerPaused = useAgentsStore((s) => s.setWorkerPaused)
  const consoleUrl = useWorkerConsoleUrl() ?? ''
  const { data: workers } = useQuery({
    queryKey: extraQueryKeys.workspaceWorkers(workspaceId ?? ''),
    queryFn: () => listAgentWorkers(workspaceId),
    enabled: !!workspaceId && enabled,
    refetchInterval: 5000,
  })
  const resumeScheduling = useCallback(() => {
    void setWorkerPaused(false, workspaceId ?? '')
  }, [setWorkerPaused, workspaceId])
  return { workers, paused, consoleUrl, resumeScheduling }
}
