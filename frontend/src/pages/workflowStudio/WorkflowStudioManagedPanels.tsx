import { AgentsPanel } from './AgentsPanel'
import { ExecutorsPanel } from './ExecutorsPanel'

// 全局对话框里的 Agent/Executor 管理面板包装：携带跳转定位（focusId），
// focusId 变化时经 key 重挂载面板以选中目标条目。
export function ManagedAgentsPanel({ focusId }: { focusId: string | null }) {
  return <AgentsPanel key={focusId ?? 'default'} initialSelectedId={focusId} />
}

export function ManagedExecutorsPanel({ focusId }: { focusId: string | null }) {
  return (
    <ExecutorsPanel key={focusId ?? 'default'} initialSelectedId={focusId} />
  )
}
