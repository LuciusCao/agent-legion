import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useJobStore } from '../stores/jobStore'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { WorkspaceStatCards } from '../components/WorkspaceStatCards'
import { JobList } from '../components/JobList'
import { EmptyStateGuide } from '../components/EmptyStateGuide'
import {
  JobActionBar,
  type JobActionBarFilter,
} from '../components/JobActionBar'
import { BatchDeleteDialog } from '../components/BatchDeleteDialog'
import { fetchPipelineDefinition } from '../api'
import type { PipelineDefinitionRecord } from '../types'
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
    getFilteredJobs,
    selectMode,
    toggleSelectMode,
    batchRerunLoading,
    batchPackageLoading,
    batchDeleteLoading,
  } = useJobStore()

  const [pipelineDefinition, setPipelineDefinition] =
    useState<PipelineDefinitionRecord | null>(null)
  const [pipelineError, setPipelineError] = useState<string | null>(null)

  useEffect(() => {
    if (workspaceId) {
      fetchWorkspaceStats(workspaceId)
    }
  }, [workspaceId, fetchWorkspaceStats])

  useEffect(() => {
    if (!workspaceId) return
    fetchJobs(workspaceId)
    const interval = setInterval(() => {
      fetchJobs(workspaceId)
    }, 5000)
    return () => clearInterval(interval)
  }, [workspaceId, fetchJobs])

  useEffect(() => {
    const pipelineKey = workspaceId
      ? workspaceStats[workspaceId]?.pipeline_key
      : undefined
    if (!pipelineKey) return
    let stale = false
    fetchPipelineDefinition(pipelineKey)
      .then((data) => {
        if (stale) return
        setPipelineDefinition(data.pipeline)
      })
      .catch((err) => {
        if (stale) return
        setPipelineError(err instanceof Error ? err.message : String(err))
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

  const pipelineNodesByKey = useMemo(() => {
    if (!pipelineDefinition) return {}
    return { [pipelineDefinition.key]: pipelineDefinition }
  }, [pipelineDefinition])

  const filters: JobActionBarFilter[] = [
    { key: 'all', label: '全选', onClick: selectAll },
    { key: 'failed', label: '仅失败', onClick: selectFailed },
    { key: 'clear', label: '取消选择', onClick: clearSelection },
  ]

  const handleRerun = async (nodeKey: string) => {
    if (!workspaceId) return
    await batchRerun(workspaceId, nodeKey)
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
            pipelineDefinition={pipelineDefinition}
            pipelineNodesByKey={pipelineNodesByKey}
            mode="batch"
            loading={
              batchRerunLoading || batchPackageLoading || batchDeleteLoading
            }
            filters={filters}
            onExitSelectMode={toggleSelectMode}
            onRerun={handleRerun}
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

      {pipelineError && (
        <p className={styles.error}>流水线定义加载失败：{pipelineError}</p>
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
