import { useEffect } from 'react'
import { useParams, useNavigate, Outlet, useLocation } from 'react-router-dom'
import { IconButton } from '@mui/material'
import { useCurrentWorkspace } from '../hooks/useWorkspaces'
import { useJobStore } from '../stores/jobStore'
import { useAgentsStore } from '../stores/agentsStore'
import { useUiStore } from '../stores/uiStore'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'
import { AddDialog } from '../components/AddDialog'
import { AgentStatusIndicator } from '../components/AgentStatusIndicator'
import { MaterialIcon } from '../components/MaterialIcon'
export default function WorkspaceLayout() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const currentWorkspace = useCurrentWorkspace()

  const { fetchWorkerStatus } = useAgentsStore()
  const {
    openAddDialog,
    addDialogOpen,
    closeAddDialog,
    addDialogContext,
    addDialogWorkspaceId,
    setWorkspacePackageDialogOpen,
    setTokenUsageDialogOpen,
    pageTitle,
    pageSubtitle,
    detailPageActions,
  } = useUiStore()
  const selectMode = useJobStore((state) => state.selectMode)
  const toggleSelectMode = useJobStore((state) => state.toggleSelectMode)
  const isDetailPage =
    workspaceId &&
    location.pathname.startsWith(`/workspaces/${workspaceId}/jobs/`)
  useEffect(() => {
    if (workspaceId) {
      fetchWorkerStatus(workspaceId)
    }
  }, [fetchWorkerStatus, workspaceId])
  const title = pageTitle || currentWorkspace?.name || workspaceId || ''
  const tokenAnalysisButton = (
    <IconButton
      size="small"
      aria-label="Token 使用分析"
      onClick={() =>
        workspaceId &&
        (isDetailPage
          ? setTokenUsageDialogOpen(true)
          : navigate(`/workspaces/${workspaceId}/token-usage`))
      }
    >
      <MaterialIcon name="analytics" />
    </IconButton>
  )
  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title={title}
          subtitle={pageSubtitle}
          home={!isDetailPage}
          backTo={isDetailPage ? `/workspaces/${workspaceId}` : undefined}
          scrolled={scrolled}
          rightActions={
            !isDetailPage ? (
              <>
                {workspaceId && (
                  <AgentStatusIndicator workspaceId={workspaceId} />
                )}
                <IconButton
                  size="small"
                  aria-label={selectMode ? '完成' : '多选'}
                  onClick={toggleSelectMode}
                  className={selectMode ? 'active-icon' : ''}
                >
                  <MaterialIcon name={selectMode ? 'close' : 'checklist'} />
                </IconButton>
                <IconButton
                  size="small"
                  aria-label="添加"
                  onClick={() => {
                    if (workspaceId) {
                      openAddDialog({ context: 'workspace', workspaceId })
                    }
                  }}
                >
                  <MaterialIcon name="add" />
                </IconButton>
                <IconButton
                  size="small"
                  aria-label="包历史"
                  onClick={() => {
                    if (workspaceId) {
                      setWorkspacePackageDialogOpen(true)
                    }
                  }}
                >
                  <MaterialIcon name="inventory_2" />
                </IconButton>
                {tokenAnalysisButton}
                <IconButton
                  size="small"
                  aria-label="Workflow Studio"
                  onClick={() =>
                    navigate(`/workspaces/${workspaceId}/workflow-studio`)
                  }
                >
                  <MaterialIcon name="account_tree" />
                </IconButton>
                <IconButton
                  size="small"
                  aria-label="设置"
                  onClick={() =>
                    navigate(`/workspaces/${workspaceId}/settings`)
                  }
                >
                  <MaterialIcon name="settings" />
                </IconButton>
              </>
            ) : (
              <>
                {tokenAnalysisButton}
                {detailPageActions}
              </>
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
