import type { Dispatch, SetStateAction } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '../../../lib/queryKeys'
import type { StudioChatSessionRecord } from './studioChatApi'
import {
  deleteStudioChatSession,
  renameStudioChatSession,
} from './studioChatSessionManageApi'

/** 会话列表管理动作（#872）：改名 / 删除。失败原样抛给调用方（会话菜单
 * 行内展示错误），成功后刷新 sessions 列表缓存。
 * 删除当前会话时先把它从缓存里摘掉、再清空选中：会话记忆 hook 在选中为空
 * 时按「记忆值 → 列表首项」回落，若列表还滞留被删会话就会把它重新选回来
 * （随后消息拉取 404）。
 * 是否清空按**落地时**的选中判定（函数式更新，#872 review R1 P2）：删除
 * 在途时切换了会话，迟到响应不得清掉新选中；在途期间切到被删会话的，
 * 落地时照样清空。 */
export function useStudioChatSessionManage(
  workspaceId: string | undefined,
  selectSession: Dispatch<SetStateAction<string | null>>
) {
  const queryClient = useQueryClient()
  const key = queryKeys.studioChatSessions(workspaceId ?? '')

  async function rename(sessionId: string, title: string): Promise<void> {
    if (!workspaceId) return
    await renameStudioChatSession(workspaceId, sessionId, title)
    await queryClient.invalidateQueries({ queryKey: key })
  }

  async function remove(sessionId: string): Promise<void> {
    if (!workspaceId) return
    await deleteStudioChatSession(workspaceId, sessionId)
    queryClient.setQueryData<StudioChatSessionRecord[]>(key, (rows) =>
      rows?.filter((row) => row.id !== sessionId)
    )
    selectSession((current) => (current === sessionId ? null : current))
    await queryClient.invalidateQueries({ queryKey: key })
  }

  return { rename, remove }
}
