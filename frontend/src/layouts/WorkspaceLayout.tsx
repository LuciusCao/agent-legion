import { useEffect } from 'react'
import { useParams, useNavigate, Outlet } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useUiStore } from '../stores/uiStore'

export const VIDEO_HIVE_ID = 'video-hive'

export default function WorkspaceLayout() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const {
    workspaces,
    currentWorkspace,
    fetchWorkspaces,
    setCurrentWorkspace,
    workspaceStats,
    fetchWorkspaceStats,
  } = useWorkspaceStore()

  const { workerPaused, fetchWorkerStatus, setWorkerPaused } = useUiStore()

  const isVideoHive = workspaceId === VIDEO_HIVE_ID

  useEffect(() => {
    if (workspaces.length === 0) {
      fetchWorkspaces()
    }
  }, [workspaces.length, fetchWorkspaces])

  useEffect(() => {
    const ws = workspaces.find((w) => w.id === workspaceId)
    setCurrentWorkspace(ws || null)
  }, [workspaceId, workspaces, setCurrentWorkspace])

  useEffect(() => {
    if (workspaceId && !isVideoHive && !workspaceStats[workspaceId]) {
      fetchWorkspaceStats(workspaceId)
    }
  }, [workspaceId, isVideoHive, workspaceStats, fetchWorkspaceStats])

  useEffect(() => {
    fetchWorkerStatus()
  }, [fetchWorkerStatus])

  const workspaceName = isVideoHive
    ? 'Video Hive'
    : currentWorkspace?.name || workspaceId

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* App bar */}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 24px',
          borderBottom: '1px solid var(--md-sys-color-outline-variant)',
          gap: 16,
          flexShrink: 0,
        }}
      >
        {/* Left */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            minWidth: 0,
          }}
        >
          <md-icon-button onClick={() => navigate('/')}>
            <md-icon>arrow_back</md-icon>
          </md-icon-button>
          <h1
            style={{
              margin: 0,
              fontSize: 20,
              fontWeight: 500,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {workspaceName}
          </h1>
          {!isVideoHive && currentWorkspace?.default_pipeline_key && (
            <span
              style={{
                padding: '4px 12px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 500,
                background: 'var(--md-sys-color-secondary-container)',
                color: 'var(--md-sys-color-on-secondary-container)',
                flexShrink: 0,
              }}
            >
              {currentWorkspace.default_pipeline_key}
            </span>
          )}
        </div>

        {/* Right */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <md-outlined-button onClick={() => setWorkerPaused(!workerPaused)}>
            {workerPaused ? '▶ 继续' : '⏸ 暂停'}
          </md-outlined-button>
          <md-filled-button
            onClick={() => navigate(`/workspaces/${workspaceId}/settings`)}
          >
            设置
          </md-filled-button>
        </div>
      </header>

      {/* Main content */}
      <main style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        <Outlet />
      </main>
    </div>
  )
}
