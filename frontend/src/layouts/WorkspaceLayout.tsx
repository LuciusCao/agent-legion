import { useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, Outlet, useLocation } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useJobStore } from '../stores/jobStore'
import { useUiStore } from '../stores/uiStore'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'
import { AddDialog } from '../components/AddDialog'
import { AgentStatusIndicator } from '../components/AgentStatusIndicator'
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
    fetchWorkspaceStats,
  } = useWorkspaceStore()

  const {
    fetchWorkerStatus,
    pageTitle,
    detailPageActions,
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
  const isDetailPage = isJobDetail

  const backTo = isDetailPage ? `/workspaces/${workspaceId}` : undefined

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
    if (workspaceId) {
      fetchWorkerStatus(workspaceId)
    }
  }, [fetchWorkerStatus, workspaceId])

  const workspaceName = isVideoHive
    ? 'Video Hive'
    : currentWorkspace?.name || workspaceId

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
                <AgentStatusIndicator workspaceId={workspaceId} />
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
                <md-icon-button
                  aria-label={WORKSPACE_LABELS.settings}
                  onClick={() =>
                    navigate(`/workspaces/${workspaceId}/settings`)
                  }
                >
                  <md-icon>settings</md-icon>
                </md-icon-button>
              </>
            ) : (
              detailPageActions
            )
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
