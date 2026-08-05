import { useParams } from 'react-router-dom'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { MonitoringPanel } from '../components/MonitoringPanel'

export function MonitoringPage() {
  // /monitoring 为全局 fleet 视图；/workspaces/:workspaceId/monitoring 为
  // workspace 作用域视图（issue：监控应随进入的 workspace 过滤）。
  const { workspaceId } = useParams<{ workspaceId: string }>()
  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title="监控"
          backTo={workspaceId ? `/workspaces/${workspaceId}` : '/'}
          scrolled={scrolled}
        />
      )}
      mainClassName="monitoring-main"
    >
      <MonitoringPanel workspaceId={workspaceId} />
    </AppShell>
  )
}

export default MonitoringPage
