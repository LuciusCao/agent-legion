import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  selectFilterCounts,
  selectFilteredJobIds,
  useJobStore,
} from '../stores/jobStore'
import { useWorkspaceEvents } from '../hooks/useWorkspaceEvents'
import { useWorkspaceStats } from '../hooks/useWorkspaceStats'
import { useJobFilterRefetch } from '../hooks/useJobFilterRefetch'
import { useWorkspacePackageActions } from '../hooks/useWorkspacePackageActions'
import { useWorkspacePauseActions } from '../hooks/useWorkspacePauseActions'
import { useWorkspaceRerunActions } from '../hooks/useWorkspaceRerunActions'
import { useWorkspaceSelection } from '../hooks/useWorkspaceSelection'
import { useWorkspaceOnboardingSteps } from '../hooks/useWorkspaceOnboardingSteps'
import { JobFilterBar } from '../components/job/JobFilterBar'
import { JobList } from '../components/job/JobList'
import { EmptyStateGuide } from '../components/EmptyStateGuide'
import {
  JobActionBar,
  type JobActionBarFilter,
} from '../components/job/JobActionBar'
import { BatchDeleteDialog } from '../components/BatchDeleteDialog'
import { WorkspacePackageHistoryDialog } from '../components/WorkspacePackageHistoryDialog'
import { useWorkflowDefinitionQuery } from '../hooks/useWorkflowDefinitionQuery'
import { toErrorMessage } from '../lib/queryError'
import styles from './WorkspaceMainPage.module.css'

export default function WorkspaceMainPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const { data: workspaceStats } = useWorkspaceStats(workspaceId)
  const jobIds = useJobStore((state) => state.jobIds)
  const filterConfig = useJobStore((state) => state.filterConfig)
  const setFilterConfig = useJobStore((state) => state.setFilterConfig)
  const selectAll = useJobStore((state) => state.selectAll)
  const selectFailed = useJobStore((state) => state.selectFailed)
  const selectUnpacked = useJobStore((state) => state.selectUnpacked)
  const clearSelection = useJobStore((state) => state.clearSelection)
  const batchDelete = useJobStore((state) => state.batchDelete)
  const batchRunTo = useJobStore((state) => state.batchRunTo)
  const filteredJobIds = useJobStore(selectFilteredJobIds)
  const selectMode = useJobStore((state) => state.selectMode)
  const toggleSelectMode = useJobStore((state) => state.toggleSelectMode)
  const batchRerunLoading = useJobStore((state) => state.batchRerunLoading)
  const batchPackageLoading = useJobStore((state) => state.batchPackageLoading)
  const batchClearPackedLoading = useJobStore(
    (state) => state.batchClearPackedLoading
  )
  const batchDeleteLoading = useJobStore((state) => state.batchDeleteLoading)
  const batchRunToLoading = useJobStore((state) => state.batchRunToLoading)
  const batchUpgradeWorkflowLoading = useJobStore(
    (state) => state.batchUpgradeWorkflowLoading
  )
  const jobsLoading = useJobStore((state) => state.isLoading)

  useWorkspaceEvents(workspaceId)
  useJobFilterRefetch(workspaceId)

  const workflowKey = workspaceStats?.workflow_key
  const { data: workflowDefinitionData, error: workflowQueryError } =
    useWorkflowDefinitionQuery(workspaceId)
  const workflowDefinition = workflowDefinitionData ?? null
  const workflowError = toErrorMessage(workflowQueryError)

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const filterCounts = useJobStore(selectFilterCounts)
  const totalJobs = useJobStore((state) => state.totalJobs) ?? jobIds.length
  const filtersActive =
    filterConfig.status !== null ||
    filterConfig.search.trim() !== '' ||
    filterConfig.workflowVersion !== null ||
    filterConfig.activeNodeKey !== null ||
    filterConfig.paused !== null
  const { selectedJobs, selectedCount, allMatchingCount } =
    useWorkspaceSelection()

  const workflowNodesByKey = useMemo(() => {
    if (!workflowDefinition) return {}
    return { [workflowDefinition.key]: workflowDefinition }
  }, [workflowDefinition])

  const filters: JobActionBarFilter[] = [
    { key: 'all', label: '全选', onClick: selectAll },
    { key: 'unpacked', label: '仅未打包', onClick: selectUnpacked },
    { key: 'failed', label: '仅失败', onClick: selectFailed },
    { key: 'clear', label: '取消选择', onClick: clearSelection },
  ]

  const { handleRerun, failureContext } = useWorkspaceRerunActions(workspaceId)

  const handleRunTo = async (targetKey: string, startKey?: string) => {
    if (workspaceId) await batchRunTo(workspaceId, targetKey, startKey)
  }

  const pauseActions = useWorkspacePauseActions(workspaceId)

  const { handlePackage, handleClearPacked, handleUpgradeWorkflow } =
    useWorkspacePackageActions(workspaceId)

  const handleDeleteConfirm = async () => {
    if (!workspaceId) return
    await batchDelete(workspaceId)
    setDeleteDialogOpen(false)
  }

  const emptyStateSteps = useWorkspaceOnboardingSteps(workspaceId, workflowKey)

  // 全新 workspace（无 job 且无筛选）只显示分步引导，隐藏筛选栏与空列表。
  const showEmptyGuide =
    filteredJobIds.length === 0 &&
    totalJobs === 0 &&
    !jobsLoading &&
    !filtersActive

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        height: '100%',
      }}
    >
      {selectMode && (
        <>
          <JobActionBar
            jobs={selectedJobs}
            selectedCount={selectedCount}
            allMatchingCount={allMatchingCount}
            workspaceId={workspaceId}
            workflowDefinition={workflowDefinition}
            workflowNodesByKey={workflowNodesByKey}
            mode="batch"
            loading={
              batchRerunLoading ||
              batchPackageLoading ||
              batchClearPackedLoading ||
              batchDeleteLoading ||
              batchRunToLoading ||
              pauseActions.pauseLoading ||
              batchUpgradeWorkflowLoading
            }
            filters={filters}
            onExitSelectMode={toggleSelectMode}
            failureContext={failureContext}
            onRerun={handleRerun}
            onRunTo={handleRunTo}
            onPackage={handlePackage}
            onClearPacked={handleClearPacked}
            onDelete={() => setDeleteDialogOpen(true)}
            onPause={pauseActions.handlePause}
            onResume={pauseActions.handleResume}
            onUpgradeWorkflow={handleUpgradeWorkflow}
          />
          <BatchDeleteDialog
            open={deleteDialogOpen}
            count={selectedCount}
            allMatching={allMatchingCount != null}
            onClose={() => setDeleteDialogOpen(false)}
            onConfirm={handleDeleteConfirm}
          />
        </>
      )}

      {workflowError && (
        <p className={styles.error}>工作流定义加载失败：{workflowError}</p>
      )}

      {showEmptyGuide && (
        <section className={styles.section}>
          <EmptyStateGuide steps={emptyStateSteps} />
        </section>
      )}

      {!showEmptyGuide && (
        <>
          <section>
            <JobFilterBar
              key={workspaceId}
              filterConfig={filterConfig}
              counts={filterCounts}
              workflowDefinition={workflowDefinition}
              onChange={setFilterConfig}
            />
          </section>

          <section className={styles.sectionFill}>
            {workspaceId ? <JobList workspaceId={workspaceId} /> : null}
          </section>
        </>
      )}

      {workspaceId && (
        <WorkspacePackageHistoryDialog workspaceId={workspaceId} />
      )}
    </div>
  )
}
