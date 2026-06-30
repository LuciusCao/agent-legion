import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useWorkspaceEvents } from '../hooks/useWorkspaceEvents'
import WorkspaceCard from '../components/WorkspaceCard'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog'
import DeleteWorkspaceDialog from '../components/DeleteWorkspaceDialog'

function WorkspaceEventSubscriber({ workspaceId }: { workspaceId: string }) {
  useWorkspaceEvents(workspaceId, true, true)
  return null
}

export function DashboardPage() {
  const navigate = useNavigate()
  const {
    workspaces,
    fetchWorkspaces,
    workspaceStats,
    fetchWorkspaceStats,
    deleteWorkspace,
  } = useWorkspaceStore()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deletingWorkspace, setDeletingWorkspace] = useState<{
    id: string
    name: string
  } | null>(null)

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

  function openDeleteDialog(id: string, name: string) {
    setDeletingWorkspace({ id, name })
    setDeleteDialogOpen(true)
  }

  function closeDeleteDialog() {
    setDeleteDialogOpen(false)
    setDeletingWorkspace(null)
  }

  async function handleDelete() {
    if (!deletingWorkspace) return
    await deleteWorkspace(deletingWorkspace.id)
  }

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
              onDelete={
                w.id === 'default'
                  ? undefined
                  : () => openDeleteDialog(w.id, w.name)
              }
            />
          </div>
        ))}
      </div>

      <CreateWorkspaceDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />

      {deletingWorkspace && (
        <DeleteWorkspaceDialog
          open={deleteDialogOpen}
          workspaceName={deletingWorkspace.name}
          workspaceId={deletingWorkspace.id}
          onClose={closeDeleteDialog}
          onConfirm={handleDelete}
        />
      )}
    </div>
  )
}
