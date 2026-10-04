import { api } from '../api'
import type { WorkerStatusResponse } from '../types'

/** 每个 workspace 独立排序：读取不能覆盖之后的操作，写请求按用户顺序执行。 */
export function createWorkerStatusActions(
  commit: (workspaceId: string, paused: boolean) => void
) {
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
    fetchWorkerStatus: async (workspaceId: string) => {
      const scope = scopeFor(workspaceId)
      const epoch = scope.epoch
      const read = ++scope.reads
      const duringWrite = scope.writes > 0
      const data = await api<WorkerStatusResponse>(
        `/api/worker/status?workspace_id=${encodeURIComponent(workspaceId)}`
      )
      // 包含“写入期间开始、写入完成后才返回”的旧快照。
      if (
        !duringWrite &&
        !scope.writes &&
        epoch === scope.epoch &&
        read === scope.reads
      ) {
        commit(workspaceId, data.paused)
      }
    },
    setWorkerPaused: (paused: boolean, workspaceId: string) => {
      const scope = scopeFor(workspaceId)
      scope.epoch++
      scope.writes++
      const operation = scope.tail.then(async () => {
        try {
          const data = await api<WorkerStatusResponse>(
            `/api/worker/${paused ? 'pause' : 'resume'}?workspace_id=${encodeURIComponent(workspaceId)}`,
            { method: 'POST' }
          )
          // 写入已串行：每个成功结果都是最新服务端状态，即使下一次写入失败也要保留。
          commit(workspaceId, data.paused)
        } finally {
          scope.writes--
        }
      })
      // 失败仍返回调用方；只让排队尾部恢复，后续重试与读取不受影响。
      scope.tail = operation.catch(() => {})
      return operation
    },
  }
}
