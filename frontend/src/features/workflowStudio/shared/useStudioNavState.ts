import { createContext, useContext, useMemo, useState } from 'react'
import type { StudioNav } from './workflowStudioNav'

// 默认 nav：宿主未挂载 Provider（深层组件单测）时退化为无偏好解析，
// pendingAgentId 为 null 不影响 capability 常规解析。
const defaultNav: StudioNav = {
  openAgent: () => {},
  pendingAgentId: null,
  clearPendingAgentId: () => {},
}

export const StudioNavContext = createContext<StudioNav>(defaultNav)

export function useStudioNav(): StudioNav {
  return useContext(StudioNavContext)
}

/** 页面层 nav 状态（StudioNavContext 的 value 工厂）：openAgent 选中绑定
 * 节点并记住目标草稿身份（pendingAgentId，节点详情解析时优先命中，
 * codex P1 on #391）；capability 无节点绑定（空 workflow）时回调提示，
 * 不再静默空转。 */
export function useStudioNavState(
  resolveNodeKey: (agentId: string) => string | null,
  selectNode: (key: string) => void,
  onNoBinding: () => void
): StudioNav {
  const [pendingAgentId, setPendingAgentId] = useState<string | null>(null)
  return useMemo(
    () => ({
      openAgent: (agentId) => {
        const key = resolveNodeKey(agentId)
        if (!key) return onNoBinding()
        setPendingAgentId(agentId)
        selectNode(key)
      },
      pendingAgentId,
      clearPendingAgentId: () => setPendingAgentId(null),
    }),
    [resolveNodeKey, selectNode, onNoBinding, pendingAgentId]
  )
}
