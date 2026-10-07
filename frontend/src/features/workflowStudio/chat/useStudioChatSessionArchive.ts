import type { Dispatch, SetStateAction } from 'react'
import { skipToken, useQuery, useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '../../../lib/queryKeys'
import type { StudioChatSessionRecord } from './studioChatApi'
import { studioChatRetentionKey } from './studioChatRetention'
import {
  archiveStudioChatSession,
  fetchArchivedStudioChatSessions,
  unarchiveStudioChatSession,
} from './studioChatSessionArchiveApi'

/** 归档视图的缓存 key 挂在 sessions key 之下：失效 sessions 列表（前缀
 * 匹配）时归档列表一并刷新，新建 / 继续对话 / 删除都不用各自记得它。 */
export function archivedStudioChatSessionsKey(workspaceId: string) {
  return [...queryKeys.studioChatSessions(workspaceId), 'archived'] as const
}

/** 会话归档（#924）：归档 / 取消归档 + 归档视图列表。
 * 归档当前会话时与删除同一套收尾（useStudioChatSessionManage）：先从默认
 * 列表缓存摘掉、再按落地时的选中清空，避免会话记忆 hook 把它重新选回来。
 * 取消归档不自动选中、不拉起 runtime：会话回到列表（已关闭），用户按既有
 * 「继续对话」恢复。失败原样抛给会话菜单行内展示。 */
export function useStudioChatSessionArchive(
  workspaceId: string | undefined,
  selectSession: Dispatch<SetStateAction<string | null>>
) {
  const queryClient = useQueryClient()
  const key = queryKeys.studioChatSessions(workspaceId ?? '')
  const archivedQuery = useQuery({
    queryKey: archivedStudioChatSessionsKey(workspaceId ?? ''),
    queryFn: async () => {
      const view = await fetchArchivedStudioChatSessions(workspaceId!)
      queryClient.setQueryData(
        studioChatRetentionKey(workspaceId!),
        view.retentionDays
      )
      return view
    },
    enabled: Boolean(workspaceId),
  })
  // 只读缓存（skipToken：本身从不拉取），由两份列表响应写入。
  const retentionQuery = useQuery<number>({
    queryKey: studioChatRetentionKey(workspaceId ?? ''),
    queryFn: skipToken,
  })

  async function archive(sessionId: string): Promise<void> {
    if (!workspaceId) return
    await archiveStudioChatSession(workspaceId, sessionId)
    queryClient.setQueryData<StudioChatSessionRecord[]>(key, (rows) =>
      rows?.filter((row) => row.id !== sessionId)
    )
    selectSession((current) => (current === sessionId ? null : current))
    await queryClient.invalidateQueries({ queryKey: key })
  }

  async function unarchive(sessionId: string): Promise<void> {
    if (!workspaceId) return
    await unarchiveStudioChatSession(workspaceId, sessionId)
    await queryClient.invalidateQueries({ queryKey: key })
  }

  return {
    archivedSessions: archivedQuery.data?.sessions ?? [],
    // #1041：实例对话保留天数。默认列表与归档列表的响应都会写入保留天数
    // 缓存（谁先到用谁）；两者都未到 / 都失败时为 null（未知）——未知不当
    // 作关闭，会话菜单在归档 / 删除提示里给通用清理警告（#1071 review）。
    retentionDays: retentionQuery.data ?? null,
    archive,
    unarchive,
  }
}
