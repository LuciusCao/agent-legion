import { useQuery } from '@tanstack/react-query'
import { fetchActiveWorkflowRevision } from '../api'
import { extraQueryKeys } from '../lib/queryKeysExtra'

/**
 * 工作流定义查询（schema v50：改读 workspace 的 active revision，
 * 全局 workflow catalog 已退役）。SettingsPage 与 WorkspaceMainPage
 * 经同一 key 共享缓存；workspace 未发布 revision 时 data 为 null
 * （404 静默），调用方按 null 定义处理（对齐原静默失败语义）。
 */
export function useWorkflowDefinitionQuery(
  workspaceId: string | null | undefined
) {
  return useQuery({
    queryKey: extraQueryKeys.workflowDefinition(workspaceId ?? ''),
    queryFn: async () => {
      try {
        return (await fetchActiveWorkflowRevision(workspaceId!)).workflow
      } catch (err) {
        if ((err as { status?: unknown }).status === 404) return null
        throw err
      }
    },
    enabled: !!workspaceId,
  })
}
