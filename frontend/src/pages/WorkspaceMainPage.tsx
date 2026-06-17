import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useJobStore } from '../stores/jobStore'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { useWorkspaceEvents } from '../hooks/useWorkspaceEvents'
import { WorkspaceStatCards } from '../components/WorkspaceStatCards'
import { JobList } from '../components/JobList'
import { EmptyStateGuide } from '../components/EmptyStateGuide'
import {
  JobActionBar,
  type JobActionBarFilter,
} from '../components/JobActionBar'
import { BatchDeleteDialog } from '../components/BatchDeleteDialog'
import { fetchWorkflowDefinition } from '../api'
import type { WorkflowDefinitionRecord } from '../types'
import styles from './WorkspaceMainPage.module.css'

const sectionStyle = {
  padding: 16,
  borderRadius: 12,
  background: 'var(--md-sys-color-surface)',
} as React.CSSProperties

export default function WorkspaceMainPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const { fetchWorkspaceStats, workspaceStats } = useWorkspaceStore()
  const {
    fetchJobs,
    jobs,
    selectedIds,
    statusFilter,
    setStatusFilter,
    searchQuery,
    setSearchQuery,
    selectAll,
    selectFailed,
    clearSelection,
    batchDelete,
    batchPackage,
    batchRerun,
    batchRunTo,
    getFilteredJobs,
    selectMode,
    toggleSelectMode,
    batchRerunLoading,
    batchPackageLoading,
    batchDeleteLoading,
    batchRunToLoading,
  } = useJobStore()

  const [workflowDefinition, setWorkflowDefinition] =
    useState<WorkflowDefinitionRecord | null>(null)
  const [workflowError, setWorkflowError] = useState<string | null>(null)

  useWorkspaceEvents(workspaceId)

  useEffect(() => {
    if (workspaceId) {
      fetchWorkspaceStats(workspaceId)
    }
  }, [workspaceId, fetchWorkspaceStats])

  useEffect(() => {
    if (!workspaceId) return
    fetchJobs(workspaceId)
  }, [workspaceId, fetchJobs])

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

  const debouncedSetSearchQuery = useDebouncedCallback(setSearchQuery, 250)
  const [searchInputValue, setSearchInputValue] = useState(searchQuery)

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const currentStats = workspaceId ? workspaceStats[workspaceId] : undefined
  const counts = useMemo(() => {
    const jobStats = currentStats?.job_stats || {}
    const pending = jobStats.pending ?? 0
    const running = jobStats.running ?? 0
    const completed = jobStats.completed ?? 0
    const failed = jobStats.failed ?? 0
    return {
      all: pending + running + completed + failed,
      pending,
      running,
      completed,
      failed,
    }
  }, [currentStats])

  const filteredJobs = getFilteredJobs()
  const totalJobs = counts.all
  const selectedJobs = jobs.filter((j) => selectedIds.has(j.id))

  const workflowNodesByKey = useMemo(() => {
    if (!workflowDefinition) return {}
    return { [workflowDefinition.key]: workflowDefinition }
  }, [workflowDefinition])

  const filters: JobActionBarFilter[] = [
    { key: 'all', label: '全选', onClick: selectAll },
    { key: 'failed', label: '仅失败', onClick: selectFailed },
    { key: 'clear', label: '取消选择', onClick: clearSelection },
  ]

  const handleRerun = async (nodeKey: string) => {
    if (!workspaceId) return
    await batchRerun(workspaceId, nodeKey)
  }

  const handleRunTo = async (targetKey: string, startKey?: string) => {
    if (!workspaceId) return
    await batchRunTo(workspaceId, targetKey, startKey)
  }

  const handlePackage = async () => {
    if (!workspaceId) return
    const result = await batchPackage(workspaceId)
    if (result.download_url) {
      window.open(result.download_url, '_blank')
    }
  }

  const handleDelete = () => {
    setDeleteDialogOpen(true)
  }

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
      }}
    >
      {selectMode && (
        <>
          <JobActionBar
            jobs={selectedJobs}
            selectedCount={selectedIds.size}
            workflowDefinition={workflowDefinition}
            workflowNodesByKey={workflowNodesByKey}
            mode="batch"
            loading={
              batchRerunLoading ||
              batchPackageLoading ||
              batchDeleteLoading ||
              batchRunToLoading
            }
            filters={filters}
            onExitSelectMode={toggleSelectMode}
            onRerun={handleRerun}
            onRunTo={handleRunTo}
            onPackage={handlePackage}
            onDelete={handleDelete}
          />
          <BatchDeleteDialog
            open={deleteDialogOpen}
            count={selectedIds.size}
            onClose={() => setDeleteDialogOpen(false)}
            onConfirm={handleDeleteConfirm}
          />
        </>
      )}

      {workflowError && (
        <p className={styles.error}>工作流定义加载失败：{workflowError}</p>
      )}

      <section style={sectionStyle}>
        <div className={styles.filterRow}>
          <WorkspaceStatCards
            counts={counts}
            activeFilter={statusFilter}
            onFilterChange={(filter) =>
              setStatusFilter(
                filter as 'all' | 'pending' | 'running' | 'completed' | 'failed'
              )
            }
          />
          <md-outlined-text-field
            type="search"
            placeholder="搜索 ID 或标题"
            value={searchInputValue}
            onInput={(e: React.FormEvent<HTMLElement>) => {
              const value = (e.target as HTMLInputElement).value
              setSearchInputValue(value)
              debouncedSetSearchQuery(value)
            }}
            style={{ width: 280, flexShrink: 0 }}
          />
        </div>
      </section>

      {filteredJobs.length === 0 && totalJobs === 0 ? (
        <section style={sectionStyle}>
          <EmptyStateGuide steps={emptyStateSteps} />
        </section>
      ) : (
        <section style={{ ...sectionStyle, flex: 1, padding: 0 }}>
          {workspaceId ? <JobList workspaceId={workspaceId} /> : null}
        </section>
      )}
    </div>
  )
}
