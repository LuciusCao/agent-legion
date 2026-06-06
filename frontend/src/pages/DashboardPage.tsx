import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useVideoStore } from '../stores/videoStore'
import { useUiStore } from '../stores/uiStore'
import WorkspaceCard from '../components/WorkspaceCard'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog'

export function DashboardPage() {
  const navigate = useNavigate()
  const {
    workspaces,
    fetchWorkspaces,
    workspaceStats,
    fetchWorkspaceStats,
    deleteWorkspace,
  } = useWorkspaceStore()
  const { videos, fetchVideos } = useVideoStore()
  const { agents } = useUiStore()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  useEffect(() => {
    fetchWorkspaces()
    fetchVideos()
  }, [fetchWorkspaces, fetchVideos])

  useEffect(() => {
    workspaces.forEach((w) => {
      if (!workspaceStats[w.id]) {
        fetchWorkspaceStats(w.id)
      }
    })
  }, [workspaces, workspaceStats, fetchWorkspaceStats])

  const videoHiveStats = {
    running: videos.filter((v) => v.status === 'running').length,
    completed: videos.filter((v) => v.status === 'completed').length,
    failed: videos.filter((v) => v.status === 'failed').length,
  }

  const videoHiveAgentStatus = {
    total: agents.length,
    busy: agents.filter((a) => a.busy).length,
    idle: agents.filter((a) => !a.busy).length,
  }

  async function handleDelete(id: string) {
    if (deletingId) return
    if (window.confirm('确定要删除此 Workspace 吗？')) {
      setDeletingId(id)
      try {
        await deleteWorkspace(id)
      } finally {
        setDeletingId(null)
      }
    }
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
        <md-filled-button onClick={() => setDialogOpen(true)}>
          新建 Workspace
        </md-filled-button>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
          gap: 16,
        }}
      >
        <WorkspaceCard
          name="Video Hive"
          pipelineLabel="视频处理流水线"
          isSystem={true}
          jobStats={videoHiveStats}
          agentStatus={videoHiveAgentStatus}
          onClick={() => navigate('/workspaces/video-hive')}
        />

        {workspaces.map((w) => (
          <WorkspaceCard
            key={w.id}
            name={w.name}
            pipelineLabel={workspaceStats[w.id]?.pipeline_label || w.default_pipeline_key}
            jobStats={workspaceStats[w.id]?.job_stats || {}}
            agentStatus={workspaceStats[w.id]?.agent_status || { total: 0, busy: 0, idle: 0 }}
            onClick={() => navigate(`/workspaces/${w.id}`)}
            onDelete={() => handleDelete(w.id)}
          />
        ))}
      </div>

      <CreateWorkspaceDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </div>
  )
}
