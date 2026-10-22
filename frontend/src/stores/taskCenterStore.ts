import { create } from 'zustand'
import type { JobBatchOperation } from '../api/jobBatchOperationsApi'

/**
 * 任务中心受理通知（#1157 phase3）：AddItemsDialog 大批量异步受理后由
 * WorkspaceLayout 挂载的 TaskAcceptToast 消费（带「查看进度 →」的持久
 * snackbar；uiStore 的瞬时 toast 不承载动作按钮，故独立成店）。
 * #946：批量 job 操作（重跑/删除/打包等 8 端点）超阈值走 202 受理时复用
 * 同一通知臂，opKind/opId 携带操作语义（文案由 TaskAcceptToast 决定）。
 */
export type AcceptedNotice = {
  workspaceId: string
  itemsTotal: number
  /** run_batches 受理（批量创建）的句柄 id。 */
  runId?: string
  /** job_batch_operations 受理（#946 批量操作）的句柄 id 与操作类型。 */
  opId?: string
  opKind?: JobBatchOperation['kind']
}

type TaskCenterState = {
  acceptedNotice: AcceptedNotice | null
  showAcceptedNotice: (notice: AcceptedNotice) => void
  clearAcceptedNotice: () => void
}

export const useTaskCenterStore = create<TaskCenterState>((set) => ({
  acceptedNotice: null,
  showAcceptedNotice: (notice) => set({ acceptedNotice: notice }),
  clearAcceptedNotice: () => set({ acceptedNotice: null }),
}))

/** 批量操作 202 受理的统一通知入口（store actions 在判别分支调用）。 */
export function notifyJobBatchAccepted(
  workspaceId: string,
  operation: JobBatchOperation
): void {
  useTaskCenterStore.getState().showAcceptedNotice({
    workspaceId,
    itemsTotal: operation.items_total,
    opId: operation.id,
    opKind: operation.kind,
  })
}
