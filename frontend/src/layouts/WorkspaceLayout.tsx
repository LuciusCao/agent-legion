import { useEffect } from 'react'
import { useParams, useNavigate, Outlet, useLocation } from 'react-router-dom'
import { IconButton } from '@mui/material'
import { useCurrentWorkspace } from '../hooks/useWorkspaces'
import { useJobStore } from '../stores/jobStore'
import { useAgentsStore } from '../stores/agentsStore'
import { useUiStore } from '../stores/uiStore'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'
import { AddItemsDialog } from '../components/AddItemsDialog'
import { AgentStatusIndicator } from '../components/AgentStatusIndicator'
import { MaterialIcon } from '../components/MaterialIcon'
import { WorkflowStudioButton } from '../components/WorkflowStudioButton'
export default function WorkspaceLayout() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const currentWorkspace = useCurrentWorkspace()

  const { fetchWorkerStatus } = useAgentsStore()
  const {
    setWorkspacePackageDialogOpen,
    setTokenUsageDialogOpen,
    addItemsDialogOpen,
    setAddItemsDialogOpen,
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
                      setAddItemsDialogOpen(true)
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
                  aria-label="质量闭环"
                  onClick={() =>
                    workspaceId &&
                    navigate(`/workspaces/${workspaceId}/quality`)
                  }
                >
                  <MaterialIcon name="add_task" />
                </IconButton>
                <WorkflowStudioButton />
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
      <AddItemsDialog
        open={addItemsDialogOpen}
        onClose={() => setAddItemsDialogOpen(false)}
        workspaceId={workspaceId}
      />
    </AppShell>
  )
}
