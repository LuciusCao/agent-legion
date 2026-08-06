import { useQuery } from '@tanstack/react-query'
import { fetchWorkflowDefinition } from '../api'
import { extraQueryKeys } from '../lib/queryKeysExtra'

/**
 * 工作流定义查询。SettingsPage（按 draft 的 workflowKey）与
 * WorkspaceMainPage（按 stats 的 workflow_key）经同一 key 共享缓存；
 * key 随 workflowKey 变化自动重取，替代原 store 里的竞态校验。
 * 加载失败时 data 为 undefined，调用方按 null 定义处理（对齐原静默失败语义）。
 */
export function useWorkflowDefinitionQuery(
  workflowKey: string | null | undefined
) {
  return useQuery({
    queryKey: extraQueryKeys.workflowDefinition(workflowKey ?? ''),
    queryFn: async () => (await fetchWorkflowDefinition(workflowKey!)).workflow,
    enabled: !!workflowKey,
  })
}
