import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useWorkspaceEvents } from '../hooks/useWorkspaceEvents'
import WorkspaceCard from '../components/WorkspaceCard'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog'

function WorkspaceEventSubscriber({ workspaceId }: { workspaceId: string }) {
  useWorkspaceEvents(workspaceId, true, true)
  return null
}

export function DashboardPage() {
  const navigate = useNavigate()
  const { workspaces, fetchWorkspaces, workspaceStats, fetchWorkspaceStats } =
    useWorkspaceStore()
  const [dialogOpen, setDialogOpen] = useState(false)

  useEffect(() => {
    fetchWorkspaces()
  }, [fetchWorkspaces])

  useEffect(() => {
    workspaces.forEach((w) => {
      if (!workspaceStats[w.id]) {
        fetchWorkspaceStats(w.id)
      }
    })
  }, [workspaces, workspaceStats, fetchWorkspaceStats])

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
        <Button variant="contained" onClick={() => setDialogOpen(true)}>
          新建 Workspace
        </Button>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
          gap: 16,
        }}
      >
        {workspaces.map((w) => (
          <div key={w.id} style={{ display: 'contents' }}>
            <WorkspaceEventSubscriber
              key={`events-${w.id}`}
              workspaceId={w.id}
            />
            <WorkspaceCard
              name={w.name}
              workflowLabel={
                workspaceStats[w.id]?.workflow_label ||
                w.default_workflow_key ||
                ''
              }
              jobStats={workspaceStats[w.id]?.job_stats || {}}
              executorStatus={
                workspaceStats[w.id]?.executor_status?.executors || []
              }
              onClick={() => navigate(`/workspaces/${w.id}`)}
            />
          </div>
        ))}
      </div>

      <CreateWorkspaceDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  )
}
