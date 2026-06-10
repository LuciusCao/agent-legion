import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate, Outlet } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useUiStore } from '../stores/uiStore'
import { AgentPanel } from '../components/AgentPanel'
import { WORKSPACE_LABELS } from '../labels'

export const VIDEO_HIVE_ID = 'video-hive'

export default function WorkspaceLayout() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const mainRef = useRef<HTMLElement>(null)
  const [scrolled, setScrolled] = useState(false)
  const {
    workspaces,
    currentWorkspace,
    fetchWorkspaces,
    setCurrentWorkspace,
    workspaceStats,
    fetchWorkspaceStats,
  } = useWorkspaceStore()

  const { fetchWorkerStatus } = useUiStore()

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

  const lastFetchedId = useRef<string | null>(null)

  const refreshStats = useCallback(() => {
    if (workspaceId) {
      lastFetchedId.current = workspaceId
      fetchWorkspaceStats(workspaceId)
    }
  }, [workspaceId, fetchWorkspaceStats])

  useEffect(() => {
    if (workspaceId && workspaceId !== lastFetchedId.current) {
      refreshStats()
    }
  }, [workspaceId, refreshStats])

  useEffect(() => {
    const handleVisibility = () => {
      if (!document.hidden && workspaceId) {
        refreshStats()
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () =>
      document.removeEventListener('visibilitychange', handleVisibility)
  }, [workspaceId, refreshStats])

  useEffect(() => {
    fetchWorkerStatus()
  }, [fetchWorkerStatus])

  useEffect(() => {
    const main = mainRef.current
    if (!main) return
    const handleScroll = () => {
      setScrolled(main.scrollTop > 0)
    }
    main.addEventListener('scroll', handleScroll, { passive: true })
    return () => main.removeEventListener('scroll', handleScroll)
  }, [workspaceId])

  const workspaceName = isVideoHive
    ? 'Video Hive'
    : currentWorkspace?.name || workspaceId

  const allowedAgentIds =
    workspaceStats[workspaceId || '']?.agent_status?.agents?.map((a) => a.id) ??
    undefined

  const headerStyle: React.CSSProperties = scrolled
    ? {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 24px',
        gap: 16,
        flexShrink: 0,
        zIndex: 1,
        boxShadow: '0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24)',
      }
    : {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 24px',
        borderBottom: '1px solid transparent',
        gap: 16,
        flexShrink: 0,
        zIndex: 1,
      }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* App bar */}
      <header style={headerStyle}>
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
            <md-icon>home</md-icon>
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
        </div>

        {/* Right */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            minWidth: 0,
          }}
        >
          <AgentPanel
            autoFetch={false}
            bare
            compact
            allowedAgentIds={allowedAgentIds}
          />
          <md-icon-button
            aria-label={WORKSPACE_LABELS.settings}
            onClick={() => navigate(`/workspaces/${workspaceId}/settings`)}
          >
            <md-icon>settings</md-icon>
          </md-icon-button>
        </div>
      </header>

      {/* Main content */}
      <main ref={mainRef} style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        <Outlet />
      </main>
    </div>
  )
}
