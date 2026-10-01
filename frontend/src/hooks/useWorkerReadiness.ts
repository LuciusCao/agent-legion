import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listAgentWorkers } from '../api'
import type { AgentWorkerSummary } from '../api'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { useAgentsStore } from '../stores/agentsStore'
import { useWorkerConsoleUrl } from './useWorkerConsoleUrl'
import { useWorkerScheduling } from './useWorkerScheduling'

export interface WorkerReadiness {
  /** 本 workspace 视角的 Worker 列表；undefined = 尚无成功数据或刷新失败。 */
  workers: AgentWorkerSummary[] | undefined
  /** workspace 调度是否暂停（顶栏「已暂停／运行中」）。 */
  paused: boolean | undefined
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
  enabled = true,
  needsWorker = true
): WorkerReadiness {
  const paused = useAgentsStore(
    (s) => s.workerPausedByWorkspace[workspaceId ?? '']
  )
  const fetchWorkerStatus = useAgentsStore((s) => s.fetchWorkerStatus)
  const status = useQuery({
    queryKey: ['workerReadinessStatus', workspaceId],
    queryFn: async () => {
      await fetchWorkerStatus(workspaceId!)
      return true
    },
    enabled: !!workspaceId && enabled,
    staleTime: 0,
  })
  const setWorkerPaused = useWorkerScheduling(workspaceId)
  const consoleUrl =
    useWorkerConsoleUrl(enabled && !!workspaceId && needsWorker) ?? ''
  const workers = useQuery({
    queryKey: extraQueryKeys.workspaceWorkers(workspaceId ?? ''),
    queryFn: () => listAgentWorkers(workspaceId),
    enabled: !!workspaceId && enabled && needsWorker,
    refetchInterval: 5000,
  })
  const resumeScheduling = useCallback(() => {
    void setWorkerPaused(false)
  }, [setWorkerPaused])
  // 后台刷新期间保留已成功快照；明确失败后停止用旧数据推导就绪/阻塞。
  return {
    workers: needsWorker ? (workers.isSuccess ? workers.data : undefined) : [],
    paused: enabled && status.isSuccess ? paused : undefined,
    consoleUrl,
    resumeScheduling,
  }
}
