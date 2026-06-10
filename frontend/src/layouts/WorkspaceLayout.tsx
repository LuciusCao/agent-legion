import { useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, Outlet, useLocation } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useJobStore } from '../stores/jobStore'
import { useUiStore } from '../stores/uiStore'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'
import { AgentPanel } from '../components/AgentPanel'
import { AddDialog } from '../components/AddDialog'
import { WORKSPACE_LABELS } from '../labels'

export const VIDEO_HIVE_ID = 'video-hive'

export default function WorkspaceLayout() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const {
    workspaces,
    currentWorkspace,
    fetchWorkspaces,
    setCurrentWorkspace,
    workspaceStats,
    fetchWorkspaceStats,
  } = useWorkspaceStore()

  const {
    fetchWorkerStatus,
    pageTitle,
    openAddDialog,
    addDialogOpen,
    closeAddDialog,
    addDialogContext,
    addDialogWorkspaceId,
  } = useUiStore()
  const selectMode = useJobStore((state) => state.selectMode)
  const toggleSelectMode = useJobStore((state) => state.toggleSelectMode)

  const isVideoHive = workspaceId === VIDEO_HIVE_ID

  const isJobDetail =
    workspaceId &&
    location.pathname.startsWith(`/workspaces/${workspaceId}/jobs/`)
  const isQuestionDetail =
    workspaceId &&
    location.pathname.startsWith(`/workspaces/${workspaceId}/questions/`)
  const isDetailPage = isJobDetail || isQuestionDetail

  const backTo = isJobDetail
    ? `/workspaces/${workspaceId}/jobs`
    : isQuestionDetail
      ? `/workspaces/${workspaceId}`
      : undefined

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

  const appBarTitle = pageTitle || workspaceName || ''

  const showListActions = !isDetailPage

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title={appBarTitle}
          home={!isDetailPage}
          backTo={backTo}
          scrolled={scrolled}
          rightActions={
            showListActions ? (
              <>
                <md-icon-button
                  aria-label={selectMode ? '完成' : '多选'}
                  onClick={toggleSelectMode}
                  className={selectMode ? 'active-icon' : ''}
                >
                  <md-icon>{selectMode ? 'close' : 'checklist'}</md-icon>
                </md-icon-button>
                <md-icon-button
                  aria-label="添加"
                  onClick={() => {
                    if (isVideoHive) {
                      openAddDialog({ context: 'video' })
                    } else if (workspaceId) {
                      openAddDialog({ context: 'workspace', workspaceId })
                    }
                  }}
                >
                  <md-icon>add</md-icon>
                </md-icon-button>
                <md-icon-button
                  aria-label="包历史"
                  onClick={() => {
                    if (workspaceId) {
                      navigate(`/workspaces/${workspaceId}/packages`)
                    }
                  }}
                >
                  <md-icon>inventory_2</md-icon>
                </md-icon-button>
                <AgentPanel
                  autoFetch={false}
                  bare
                  compact
                  allowedAgentIds={allowedAgentIds}
                />
                <md-icon-button
                  aria-label={WORKSPACE_LABELS.settings}
                  onClick={() =>
                    navigate(`/workspaces/${workspaceId}/settings`)
                  }
                >
                  <md-icon>settings</md-icon>
                </md-icon-button>
              </>
            ) : undefined
          }
        />
      )}
      mainClassName="workspace-main"
    >
      <Outlet />
      <AddDialog
        open={addDialogOpen}
        onClose={closeAddDialog}
        context={addDialogContext}
        workspaceId={addDialogWorkspaceId}
      />
    </AppShell>
  )
}
