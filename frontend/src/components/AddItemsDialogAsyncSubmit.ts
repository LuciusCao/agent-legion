import type { QueryClient } from '@tanstack/react-query'
import { createRunAsync } from '../api'
import type { AcceptedNotice } from '../stores/taskCenterStore'
import { runBatchKeys } from '../features/taskCenter/useRunBatches'
import type { RunItem } from '../types'

/**
 * 大批量提交的异步受理臂（#1157 phase3）：202 句柄秒级返回后——受理通知
 * （TaskAcceptToast 消费，带「查看进度 →」）+ 任务中心查询失效（AppBar
 * 徽标立即刷新并 pulse）。从 AddItemsDialog 拆出（体积预算，同
 * AddItemsDialogSubmitText 先例）。
 */
export async function acceptRunItemsAsync(
  workspaceId: string,
  items: RunItem[],
  queryClient: QueryClient,
  showAcceptedNotice: (notice: AcceptedNotice) => void
): Promise<void> {
  const accepted = await createRunAsync(workspaceId, { items })
  showAcceptedNotice({
    workspaceId,
    itemsTotal: items.length,
    runId: accepted.run.id,
  })
  queryClient.invalidateQueries({ queryKey: runBatchKeys.all(workspaceId) })
}
