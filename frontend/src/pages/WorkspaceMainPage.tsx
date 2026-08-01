import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceEvents } from '../hooks/useWorkspaceEvents'
import { useJobFilterRefetch } from './useJobFilterRefetch'
import { useWorkspacePackageActions } from './useWorkspacePackageActions'
import { useWorkspaceRerunActions } from './useWorkspaceRerunActions'
import { useWorkspaceSelection } from './useWorkspaceSelection'
import { JobFilterBar } from '../components/JobFilterBar'
import { JobList } from '../components/JobList'
import { EmptyStateGuide } from '../components/EmptyStateGuide'
import {
  JobActionBar,
  type JobActionBarFilter,
} from '../components/JobActionBar'
import { BatchDeleteDialog } from '../components/BatchDeleteDialog'
import { WorkspacePackageHistoryDialog } from '../components/WorkspacePackageHistoryDialog'
import { fetchWorkflowDefinition } from '../api'
import {
  selectFilterCounts,
  selectFilteredJobIds,
} from '../stores/job/selectors'
import type { WorkflowDefinitionRecord } from '../types'
import styles from './WorkspaceMainPage.module.css'

export default function WorkspaceMainPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const { fetchWorkspaceStats, workspaceStats } = useWorkspaceStore()
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

  const [workflowDefinition, setWorkflowDefinition] =
    useState<WorkflowDefinitionRecord | null>(null)
  const [workflowError, setWorkflowError] = useState<string | null>(null)
  useWorkspaceEvents(workspaceId)
  useJobFilterRefetch(workspaceId)

  useEffect(() => {
    if (workspaceId) {
      fetchWorkspaceStats(workspaceId)
    }
  }, [workspaceId, fetchWorkspaceStats])

  useEffect(() => {
    const workflowKey = workspaceId
      ? workspaceStats[workspaceId]?.workflow_key
      : undefined
    if (!workflowKey) return
    let stale = false
    fetchWorkflowDefinition(workflowKey)
      .then((data) => {
        if (stale) return
        setWorkflowDefinition(data.workflow)
      })
      .catch((err) => {
        if (stale) return
        setWorkflowError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      stale = true
    }
  }, [workspaceId, workspaceStats])

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const filterCounts = useJobStore(selectFilterCounts)
  const totalJobs = useJobStore((state) => state.totalJobs) ?? jobIds.length
  const filtersActive =
    filterConfig.status !== null ||
    filterConfig.search.trim() !== '' ||
    filterConfig.workflowVersion !== null ||
    filterConfig.activeNodeKey !== null
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

  const { handlePackage, handleClearPacked, handleUpgradeWorkflow } =
    useWorkspacePackageActions(workspaceId)

  const handleDeleteConfirm = async () => {
    if (!workspaceId) return
    await batchDelete(workspaceId)
    setDeleteDialogOpen(false)
  }

  const emptyStateSteps = useMemo(
    () => [
      {
        icon: 'settings',
        title: '开始使用 Workspace',
        description:
          '当前 Workspace 还没有任务，先前往设置页配置资源连接与接入模式。',
        unlocked: true,
        actionLabel: '去配置',
        onAction: () => navigate('settings'),
      },
    ],
    [navigate]
  )

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
            workflowDefinition={workflowDefinition}
            workflowNodesByKey={workflowNodesByKey}
            mode="batch"
            loading={
              batchRerunLoading ||
              batchPackageLoading ||
              batchClearPackedLoading ||
              batchDeleteLoading ||
              batchRunToLoading ||
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

      <section>
        <JobFilterBar
          key={workspaceId}
          filterConfig={filterConfig}
          counts={filterCounts}
          workflowDefinition={workflowDefinition}
          onChange={setFilterConfig}
        />
      </section>

      {filteredJobIds.length === 0 &&
      totalJobs === 0 &&
      !jobsLoading &&
      !filtersActive ? (
        <section className={styles.section}>
          <EmptyStateGuide steps={emptyStateSteps} />
        </section>
      ) : (
        <section className={styles.sectionFill}>
          {workspaceId ? <JobList workspaceId={workspaceId} /> : null}
        </section>
      )}

      {workspaceId && (
        <WorkspacePackageHistoryDialog workspaceId={workspaceId} />
      )}
    </div>
  )
}
