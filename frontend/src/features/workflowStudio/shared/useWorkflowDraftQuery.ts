import { useQuery } from '@tanstack/react-query'
import { fetchWorkflowDraft } from '../../../api'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

/** 服务端草稿查询（GET workflow-draft）：definition_yaml 为 null 表示该
 * workspace 还没有持久化草稿；查询失败（data 停留 undefined）时草稿退回
 * 纯内存行为，由组合层把 isError 合并进保存状态做可见警示。重试沿用全局
 * QueryClient 策略（5xx/网络错误最多 2 次，见 lib/queryClient.ts）。 */
export function useWorkflowDraftQuery(workspaceId: string | undefined) {
  return useQuery({
    queryKey: extraQueryKeys.workflowStudioDraft(workspaceId ?? ''),
    queryFn: () => fetchWorkflowDraft(workspaceId ?? ''),
    enabled: !!workspaceId,
  })
}
