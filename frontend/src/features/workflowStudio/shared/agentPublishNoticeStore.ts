import { create } from 'zustand'
import type { StudioPublishRequestRecord } from '../../../api/studioPublishRequestApi'

/** #416/#429：agent 发布请求落定回执的跨组件共享层。原来 resolvedNotice 是
 * useAgentPublishRequest 内的 useState，每个 hook 调用点一份——对话框实例
 * 写入的回执，对话栏实例永远读不到（死功能）。提升为 zustand store 后，
 * 两个实例共享同一份回执：确认/取消在对话框里发生，栏顶（StudioChatAside）
 * 同轮可见。notice 带 id 供 React key（同一文案连发两轮也能触发重渲染）。
 * 二轮复审追加 lastResolvedRequestId：resolve 归属也必须共享——否则旁观
 * hook 实例的观测 effect 会用「已消解」覆盖操作实例刚着陆的正确回执。 */
export interface AgentPublishResolvedNotice {
  id: number
  message: string
}

interface AgentPublishNoticeState {
  resolvedNotice: AgentPublishResolvedNotice | null
  /** 本 app 内任一 hook 实例最近一次主动 resolve（confirm/cancel）掉的
   * 请求 id（#429 二轮复审）：观测 pending→null 跳变时，跳变的请求若
   * 命中它，说明正确回执已由操作实例着陆——旁观实例不得再覆盖。per-
   * instance ref 挡不住旁观者：生产拓扑里对话框与栏顶两个实例共享
   * QueryClient，invalidate 后双方同时收到 pending→null。 */
  lastResolvedRequestId: string | null
  landNotice: (message: string) => void
  clearNotice: () => void
  /** confirm/cancel 成功后记录本次主动 resolve 的请求 id。 */
  markResolved: (requestId: string) => void
}

let nextNoticeId = 1

export const useAgentPublishNoticeStore = create<AgentPublishNoticeState>(
  (set) => ({
    resolvedNotice: null,
    lastResolvedRequestId: null,
    landNotice: (message) =>
      set({ resolvedNotice: { id: nextNoticeId++, message } }),
    clearNotice: () => set({ resolvedNotice: null }),
    markResolved: (requestId) => set({ lastResolvedRequestId: requestId }),
  })
)

/** status → 回执文案（#429）。result_revision_id 仅在产生了新 revision 时
 * 非空（后端契约对齐）：runtime-only 保存走「未产生新版本」文案；被顶替
 * （agent 重发 / 手动发布取代）与拒绝各有独立文案。 */
export function agentPublishStatusNotice(
  request: StudioPublishRequestRecord
): string | null {
  switch (request.status) {
    case 'confirmed':
      return request.result_revision_id
        ? `已按 Agent 请求发布（revision ${request.result_revision_id}）`
        : '已按 Agent 请求保存节点运行配置（未产生新版本）'
    case 'rejected':
      return '已拒绝 Agent 的发布请求，Agent 可继续修改草稿'
    case 'superseded':
      return 'Agent 的发布请求已被顶替（新请求或手动发布已取代它）'
    default:
      return null
  }
}
