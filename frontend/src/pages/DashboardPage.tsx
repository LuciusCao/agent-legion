import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useVideoStore } from '../stores/videoStore'

export function DashboardPage() {
  const navigate = useNavigate()
  const { workspaces, fetchWorkspaces, workspaceStats, fetchWorkspaceStats } =
    useWorkspaceStore()
  const { videos, fetchVideos } = useVideoStore()
  const [dialogOpen, setDialogOpen] = useState(false)

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
    total: videos.length,
    running: videos.filter((v) => v.status === 'running').length,
    completed: videos.filter((v) => v.status === 'completed').length,
    failed: videos.filter((v) => v.status === 'failed').length,
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
        {/* Video Hive system card */}
        <div
          onClick={() => navigate('/workspaces/video-hive')}
          style={{
            borderRadius: 16,
            padding: 20,
            background: 'var(--md-sys-color-surface-container-low)',
            cursor: 'pointer',
            border: '1px solid var(--md-sys-color-outline-variant)',
          }}
        >
          <h3 style={{ margin: '0 0 4px', fontSize: 20 }}>Video Hive</h3>
          <span style={{ fontSize: 12, color: 'var(--md-sys-color-on-surface-variant)' }}>
            视频处理流水线
          </span>
          <div style={{ marginTop: 16, fontSize: 13 }}>
            Jobs: {videoHiveStats.total} | {videoHiveStats.running} 运行中 | {videoHiveStats.completed} 已完成 | {videoHiveStats.failed} 失败
          </div>
          <div style={{ marginTop: 16 }}>
            <md-filled-button style={{ width: '100%' }}>进入</md-filled-button>
          </div>
        </div>

        {/* Regular workspace cards */}
        {workspaces.map((w) => (
          <div
            key={w.id}
            onClick={() => navigate(`/workspaces/${w.id}`)}
            style={{
              borderRadius: 16,
              padding: 20,
              background: 'var(--md-sys-color-surface-container-low)',
              cursor: 'pointer',
              border: '1px solid var(--md-sys-color-outline-variant)',
            }}
          >
            <h3 style={{ margin: '0 0 4px', fontSize: 20 }}>{w.name}</h3>
            <span style={{ fontSize: 12, color: 'var(--md-sys-color-on-surface-variant)' }}>
              {workspaceStats[w.id]?.pipeline_label || w.default_pipeline_key}
            </span>
            <div style={{ marginTop: 16, fontSize: 13 }}>
              {/* Show basic stats if available */}
              {workspaceStats[w.id]
                ? `Jobs: ${Object.values(workspaceStats[w.id].job_stats).reduce((a, b) => a + b, 0)}`
                : '加载中…'}
            </div>
            <div style={{ marginTop: 16 }}>
              <md-filled-button style={{ width: '100%' }}>进入</md-filled-button>
            </div>
          </div>
        ))}
      </div>

      {/* Create workspace dialog placeholder */}
      {dialogOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 100,
          }}
          onClick={() => setDialogOpen(false)}
        >
          <div
            style={{
              background: 'var(--md-sys-color-surface)',
              padding: 24,
              borderRadius: 16,
              minWidth: 320,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: '0 0 16px' }}>新建 Workspace</h3>
            <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
              对话框组件将在后续 Task 中实现
            </p>
            <div style={{ marginTop: 16, textAlign: 'right' }}>
              <md-text-button onClick={() => setDialogOpen(false)}>关闭</md-text-button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
