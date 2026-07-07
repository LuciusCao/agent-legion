import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@mui/material'
import { useWorkspaceStore } from '../stores/workspaceStore'
import type { WorkspaceStats } from '../workspaceTypes'
import WorkspaceCard from '../components/WorkspaceCard'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog'

interface DashboardStatsPayload {
  type: string
  workspaces: Array<{ id: string } & WorkspaceStats>
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

  useEffect(() => {
    if (typeof EventSource === 'undefined') return
    const source = new EventSource('/api/dashboard/events')
    source.onmessage = (event) => {
      if (!event.data || event.data.startsWith(':heartbeat')) return
      try {
        const payload = JSON.parse(event.data) as DashboardStatsPayload
        if (payload.type !== 'workspace_stats_batch') return
        for (const workspace of payload.workspaces) {
          const { id, ...stats } = workspace
          useWorkspaceStore.getState().setWorkspaceStats(id, stats)
        }
      } catch {
        // ignore invalid payloads
      }
    }
    return () => source.close()
  }, [])

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
          <WorkspaceCard
            key={w.id}
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
        ))}
      </div>

      <CreateWorkspaceDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  )
}
