import { useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, Outlet } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useUiStore } from '../stores/uiStore'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'
import { AgentPanel } from '../components/AgentPanel'
import { WORKSPACE_LABELS } from '../labels'

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

  const workspaceName = isVideoHive
    ? 'Video Hive'
    : currentWorkspace?.name || workspaceId

  const allowedAgentIds =
    workspaceStats[workspaceId || '']?.agent_status?.agents?.map((a) => a.id) ??
    undefined

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title={workspaceName || ''}
          home
          scrolled={scrolled}
          rightActions={
            <>
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
            </>
          }
        />
      )}
      mainClassName="workspace-main"
    >
      <Outlet />
    </AppShell>
  )
}
