import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { useWorkspaces } from '../hooks/useWorkspaces'
import { useWorkspaceStats } from '../hooks/useWorkspaceStats'
import { useDashboardEvents } from '../hooks/useDashboardEvents'
import WorkspaceCard from '../components/WorkspaceCard'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog'
import { UserMenu } from '../components/UserMenu'
import type { WorkspaceRecord } from '../types'

function DashboardWorkspaceCard({ workspace }: { workspace: WorkspaceRecord }) {
  const navigate = useNavigate()
  const { data: stats } = useWorkspaceStats(workspace.id)
  return (
    <WorkspaceCard
      name={workspace.name}
      workflowLabel={
        stats?.workflow_label || workspace.default_workflow_key || ''
      }
      jobStats={stats?.job_stats || {}}
      codePool={stats?.code_pool}
      onClick={() => navigate(`/workspaces/${workspace.id}`)}
    />
  )
}

export function DashboardPage() {
  const { data: workspaces = [] } = useWorkspaces()
  const [dialogOpen, setDialogOpen] = useState(false)

  useDashboardEvents()

  return (
    <div style={{ padding: 24 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 28 }}>Agent Legion</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <UserMenu />
          <Button variant="contained" onClick={() => setDialogOpen(true)}>
            新建 Workspace
          </Button>
        </div>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
          gap: 16,
        }}
      >
        {workspaces.map((w) => (
          <DashboardWorkspaceCard key={w.id} workspace={w} />
        ))}
      </div>

      <CreateWorkspaceDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  )
}
