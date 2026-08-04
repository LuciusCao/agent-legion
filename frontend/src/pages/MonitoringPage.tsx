import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { MonitoringPanel } from '../components/MonitoringPanel'

export function MonitoringPage() {
  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar title="监控" backTo="/" scrolled={scrolled} />
      )}
      mainClassName="monitoring-main"
    >
      <MonitoringPanel />
    </AppShell>
  )
}

export default MonitoringPage
