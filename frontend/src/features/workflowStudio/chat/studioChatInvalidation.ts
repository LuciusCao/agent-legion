import type { QueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

/** agent 一轮（turn_end）结束后的查询失效：可能已保存 workflow/Agent 草稿
 * 或提交新的 skill 版本——失效画布基线、Agent 目录、Agent 定义列表（#387：
 * MCP 的 save_agent_definition_draft 会新建 draft-only Agent）与技能预览
 * 查询（按前缀覆盖所有 skill/ref），MCP 修改无需手动刷新即反映到 DAG 与
 * 预览 panel。 */
export function invalidateStudioTurnEndQueries(
  queryClient: QueryClient,
  workspaceId: string
) {
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.workflowStudioData(workspaceId),
  })
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.studioAgentCatalog(workspaceId),
  })
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.agentDefinitions(workspaceId),
  })
  void queryClient.invalidateQueries({ queryKey: ['studioSkillDetail'] })
}
