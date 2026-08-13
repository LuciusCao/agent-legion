import { createContext, useContext } from 'react'

// Studio 内「跳到 Agent / Executor 管理」的导航通道：由页面层提供，
// Inspector 深层组件直接消费，避免跨多层面板逐层透传回调。
export type StudioNav = {
  openAgent: (agentId: string) => void
  openExecutor: (executorId: string) => void
}

const defaultNav: StudioNav = {
  openAgent: () => {},
  openExecutor: () => {},
}

export const StudioNavContext = createContext<StudioNav>(defaultNav)

export function useStudioNav(): StudioNav {
  return useContext(StudioNavContext)
}
