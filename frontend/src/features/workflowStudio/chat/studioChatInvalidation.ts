import type { QueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

/** agent 一轮（turn_end）结束后的查询失效：可能已保存 workflow 草稿或
 * 提交新的 skill 版本——失效画布基线、服务端草稿（#633：MCP 的
 * save_workflow_draft 直接写画布草稿，失效后 useWorkflowDraftQuery 重取，
 * useServerDraftApply 在用户未触碰本地草稿时应用新值）、Agent 目录与技能
 * 预览查询（按前缀覆盖所有 skill/ref），MCP 修改无需手动刷新即反映到 DAG
 * 与预览 panel。#1079（#440 P3b）：Agent 定义写工具自 P3 起不写库，turn
 * 结束不再失效 Agent 定义列表。 */
export function invalidateStudioTurnEndQueries(
  queryClient: QueryClient,
  workspaceId: string
) {
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.workflowStudioData(workspaceId),
  })
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.workflowStudioDraft(workspaceId),
  })
  void queryClient.invalidateQueries({
    queryKey: extraQueryKeys.studioAgentCatalog(workspaceId),
  })
  void queryClient.invalidateQueries({ queryKey: ['studioSkillDetail'] })
}
