import { useEffect, useState } from 'react'
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

  // Field selectors only: whole-store subscriptions here would re-render the
  // page skeleton + <Outlet/> subtree on every unrelated store write (agent
  // heartbeats, toast flags).
  const fetchWorkerStatus = useAgentsStore((s) => s.fetchWorkerStatus)
  const setWorkspacePackageDialogOpen = useUiStore(
    (s) => s.setWorkspacePackageDialogOpen
  )
  const setTokenUsageDialogOpen = useUiStore((s) => s.setTokenUsageDialogOpen)
  const pageTitle = useUiStore((s) => s.pageTitle)
  const pageSubtitle = useUiStore((s) => s.pageSubtitle)
  const detailPageActions = useUiStore((s) => s.detailPageActions)
  const selectMode = useJobStore((state) => state.selectMode)
  const toggleSelectMode = useJobStore((state) => state.toggleSelectMode)
  const [addItemsOpen, setAddItemsOpen] = useState(false)
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
                      setAddItemsOpen(true)
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
        open={addItemsOpen}
        onClose={() => setAddItemsOpen(false)}
        workspaceId={workspaceId}
      />
    </AppShell>
  )
}
