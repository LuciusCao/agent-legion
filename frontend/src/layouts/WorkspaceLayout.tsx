import { useEffect } from 'react'
import { useParams, useNavigate, Outlet, useLocation } from 'react-router-dom'
import { useCurrentWorkspace } from '../hooks/useWorkspaces'
import { useJobStore } from '../stores/jobStore'
import { useAgentsStore } from '../stores/agentsStore'
import { useUiStore } from '../stores/uiStore'
import { AppShell } from './AppShell'
import { AppBar } from '../components/AppBar'
import { AddItemsDialog } from '../components/AddItemsDialog'
import { AgentStatusIndicator } from '../components/AgentStatusIndicator'
import { LabeledIconButton } from '../components/LabeledIconButton'
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
  const addItemsDialogOpen = useUiStore((s) => s.addItemsDialogOpen)
  const setAddItemsDialogOpen = useUiStore((s) => s.setAddItemsDialogOpen)
  const pageTitle = useUiStore((s) => s.pageTitle)
  const pageSubtitle = useUiStore((s) => s.pageSubtitle)
  const detailPageActions = useUiStore((s) => s.detailPageActions)
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
    <LabeledIconButton
      icon="analytics"
      label="用量"
      ariaLabel="Token 使用分析"
      onClick={() =>
        workspaceId &&
        (isDetailPage
          ? setTokenUsageDialogOpen(true)
          : navigate(`/workspaces/${workspaceId}/token-usage`))
      }
    />
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
                <LabeledIconButton
                  icon={selectMode ? 'close' : 'checklist'}
                  label={selectMode ? '完成' : '多选'}
                  active={selectMode}
                  onClick={toggleSelectMode}
                />
                <LabeledIconButton
                  icon="add"
                  label="添加"
                  onClick={() => {
                    if (workspaceId) {
                      setAddItemsDialogOpen(true)
                    }
                  }}
                />
                <LabeledIconButton
                  icon="inventory_2"
                  label="打包"
                  ariaLabel="包历史"
                  onClick={() => {
                    if (workspaceId) {
                      setWorkspacePackageDialogOpen(true)
                    }
                  }}
                />
                {tokenAnalysisButton}
                <LabeledIconButton
                  icon="add_task"
                  label="质量"
                  ariaLabel="质量闭环"
                  onClick={() =>
                    workspaceId &&
                    navigate(`/workspaces/${workspaceId}/quality`)
                  }
                />
                <WorkflowStudioButton />
                <LabeledIconButton
                  icon="settings"
                  label="设置"
                  onClick={() =>
                    navigate(`/workspaces/${workspaceId}/settings`)
                  }
                />
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
