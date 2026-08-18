import { AgentsPanel } from './AgentsPanel'

// 全局对话框里的 Agent 管理面板包装：携带跳转定位（focusId），focusId 变化
// 时经 key 重挂载面板以选中目标条目。（P-0.5：Executor 管理面已退役。）
export function ManagedAgentsPanel({ focusId }: { focusId: string | null }) {
  return <AgentsPanel key={focusId ?? 'default'} initialSelectedId={focusId} />
}
