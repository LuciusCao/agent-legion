// Studio 对话保留策略（#1041）的前端现算：实例配置的保留天数随会话列表
// 下发（0 = 未配置），归档行倒计时 = 归档时间 + 保留天数 − 现在。后端
// sweeper 按小时级节奏清理，所以剩余不足一天时显示「即将清理」。
// 保留天数三态：number = 已知（0 即关闭）；null = 未知（默认列表与归档
// 列表的响应都还没拿到或都失败了）——未知不等于关闭，归档 / 删除提示
// 给出通用的清理警告，不能静默（不可逆删除前的告警，#1041 review）。

import type { QueryClient } from '@tanstack/react-query'
import { queryKeys } from '../../../lib/queryKeys'
import {
  fetchStudioChatSessions,
  type StudioChatSessionRecord,
} from './studioChatApi'

const DAY_MS = 86_400_000

/** 未知保留策略时的通用清理提示。 */
export const UNKNOWN_RETENTION_NOTICE =
  '若管理员配置了保留策略，到期将被自动清理'

/** 保留天数的缓存 key：默认列表与归档列表的响应各自写入（谁先到用谁）。
 * 挂在 sessions key 之下，随 sessions 前缀失效一并标记过期（本身不拉取）。 */
export function studioChatRetentionKey(workspaceId: string) {
  return [...queryKeys.studioChatSessions(workspaceId), 'retention'] as const
}

/** 默认会话列表的 queryFn：列表缓存只存会话行，响应里的保留天数顺带
 * 写入保留天数缓存（归档列表未到 / 失败时会话菜单仍知道真实天数）。 */
export function sessionsWithRetention(
  queryClient: QueryClient,
  workspaceId: string
): Promise<StudioChatSessionRecord[]> {
  return fetchStudioChatSessions(workspaceId, (days) =>
    queryClient.setQueryData(studioChatRetentionKey(workspaceId), days)
  )
}

/** 剩余整天数（向上取整、下限 0）；未配置保留策略或缺时间戳时为 null，
 * 调用方据此不渲染任何倒计时。 */
export function retentionDaysLeft(
  stamp: string | null | undefined,
  retentionDays: number,
  now: number = Date.now()
): number | null {
  if (retentionDays <= 0 || !stamp) return null
  const purgeAt = Date.parse(stamp) + retentionDays * DAY_MS
  if (Number.isNaN(purgeAt)) return null
  return Math.max(0, Math.ceil((purgeAt - now) / DAY_MS))
}

/** 归档行倒计时文案。 */
export function retentionCountdownLabel(daysLeft: number): string {
  return daysLeft <= 0 ? '即将清理' : `${daysLeft} 天后清理`
}

/** 归档 / 删除操作时的提示后缀：已配置给出天数；未知给出通用警告；
 * 已知关闭（0）为空串。 */
export function retentionNotice(retentionDays: number | null): string {
  if (retentionDays === null) return UNKNOWN_RETENTION_NOTICE
  return retentionDays > 0 ? `将于 ${retentionDays} 天后自动清理` : ''
}
