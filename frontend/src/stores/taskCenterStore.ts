import { create } from 'zustand'

/**
 * 任务中心受理通知（#1157 phase3）：AddItemsDialog 大批量异步受理后由
 * WorkspaceLayout 挂载的 TaskAcceptToast 消费（带「查看进度 →」的持久
 * snackbar；uiStore 的瞬时 toast 不承载动作按钮，故独立成店）。
 */
export type AcceptedNotice = {
  workspaceId: string
  itemsTotal: number
  runId: string
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
