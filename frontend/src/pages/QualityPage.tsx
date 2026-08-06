import { useParams } from 'react-router-dom'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { QualityPanel } from '../components/quality/QualityPanel'
import { useCurrentWorkspace } from '../hooks/useWorkspaces'
import styles from './QualityPage.module.css'

export function QualityPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const currentWorkspace = useCurrentWorkspace()
  const title = `${currentWorkspace?.name || workspaceId} / 质量闭环`

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
      mainClassName={styles.main}
    >
      <QualityPanel workspaceId={workspaceId} />
    </AppShell>
  )
}

export default QualityPage
