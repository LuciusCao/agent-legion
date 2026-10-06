import { api } from '../api'
import type { WorkerStatusResponse } from '../types'

/** 一次状态读取的结果：superseded = 读取期间/之后发生过写入或更新的读取，
 * 这份快照已过时，调用方（React Query 的 queryFn）应保留缓存里的值。 */
export interface WorkerStatusRead {
  paused: boolean
  superseded: boolean
}

/** 每个 workspace 独立排序：读取不能覆盖之后的操作，写请求按用户顺序执行。
 * #961：本模块只负责请求排序，不持有暂停位——显示的唯一数据源是 React
 * Query 缓存（useWorkerPausedStatus），写入结果由调用方写回该缓存。 */
export function createWorkerStatusActions() {
  const scopes = new Map<
    string,
    {
      epoch: number
      reads: number
      writes: number
      tail: Promise<void>
    }
  >()
  const scopeFor = (id: string) => {
    let scope = scopes.get(id)
    if (!scope) {
      scope = { epoch: 0, reads: 0, writes: 0, tail: Promise.resolve() }
      scopes.set(id, scope)
    }
    return scope
  }
  return {
    fetchWorkerStatus: async (
      workspaceId: string
    ): Promise<WorkerStatusRead> => {
      const scope = scopeFor(workspaceId)
      const epoch = scope.epoch
      const read = ++scope.reads
      const duringWrite = scope.writes > 0
      const data = await api<WorkerStatusResponse>(
        `/api/worker/status?workspace_id=${encodeURIComponent(workspaceId)}`
      )
      // 包含“写入期间开始、写入完成后才返回”的旧快照。
      const superseded =
        duringWrite ||
        scope.writes > 0 ||
        epoch !== scope.epoch ||
        read !== scope.reads
      return { paused: data.paused, superseded }
    },
    setWorkerPaused: (paused: boolean, workspaceId: string) => {
      const scope = scopeFor(workspaceId)
      scope.epoch++
      scope.writes++
      // 写入已串行：每个成功结果都是最新服务端状态，即使下一次写入失败也
      // 要保留——返回值由调用方立即写回缓存。
      const operation = scope.tail.then(async () => {
        try {
          const data = await api<WorkerStatusResponse>(
            `/api/worker/${paused ? 'pause' : 'resume'}?workspace_id=${encodeURIComponent(workspaceId)}`,
            { method: 'POST' }
          )
          return data.paused
        } finally {
          scope.writes--
        }
      })
      // 失败仍返回调用方；只让排队尾部恢复，后续重试与读取不受影响。
      scope.tail = operation.then(
        () => {},
        () => {}
      )
      return operation
    },
  }
}
