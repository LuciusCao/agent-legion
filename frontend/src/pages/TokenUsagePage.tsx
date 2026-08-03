import { useParams } from 'react-router-dom'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { TokenUsagePanel } from '../components/tokenUsage/TokenUsagePanel'
import { useWorkspaceStore } from '../stores/workspaceStore'

export function TokenUsagePage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const { currentWorkspace } = useWorkspaceStore()
  const title = `${currentWorkspace?.name || workspaceId} / Token 使用分析`

  if (!workspaceId) return null

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title={title}
          backTo={`/workspaces/${workspaceId}`}
          scrolled={scrolled}
        />
      )}
      mainClassName="token-usage-main"
    >
      <TokenUsagePanel workspaceId={workspaceId} />
    </AppShell>
  )
}
