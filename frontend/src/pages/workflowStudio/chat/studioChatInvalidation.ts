import type { QueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

/** agent 一轮（turn_end）结束后的查询失效：可能已保存 workflow/Agent 草稿
 * 或提交新的 skill 版本——失效画布基线、Agent 目录与技能预览查询（按前缀
 * 覆盖所有 skill/ref），MCP 修改无需手动刷新即反映到 DAG 与预览 panel。 */
export function invalidateStudioTurnEndQueries(
  queryClient: QueryClient,
  workspaceId: string
) {
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.workflowStudioData(workspaceId),
  })
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.studioExecutorCatalog(workspaceId),
  })
  void queryClient.invalidateQueries({ queryKey: ['studioSkillDetail'] })
}
