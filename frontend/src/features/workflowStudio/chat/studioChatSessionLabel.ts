import type { StudioChatSessionRecord } from './studioChatApi'
import { formatDateTime } from '../../../lib/formatters'

/** 会话显示名：标题为空回落「对话 <创建时间>」（#872）。会话菜单与归档区
 * （#924）共用，独立成模块避免两个组件互相 import。 */
export function sessionLabel(session: StudioChatSessionRecord): string {
  if (session.title) return session.title
  return `对话 ${formatDateTime(session.created_at)}`
}
