import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useJobStore } from '../stores/jobStore'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { WorkspaceStatCards } from '../components/WorkspaceStatCards'
import { JobList } from '../components/JobList'
import { EmptyStateGuide } from '../components/EmptyStateGuide'
import {
  BatchToolbar,
  type BatchFilter,
  type BatchAction,
} from '../components/BatchToolbar'
import { BatchDeleteDialog } from '../components/BatchDeleteDialog'
import { BatchRerunDialog } from '../components/BatchRerunDialog'
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
  } = useJobStore()

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

  const debouncedSetSearchQuery = useDebouncedCallback(setSearchQuery, 250)
  const [searchInputValue, setSearchInputValue] = useState(searchQuery)

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [rerunDialogOpen, setRerunDialogOpen] = useState(false)

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

  const filters: BatchFilter[] = [
    { key: 'all', label: '全选', onClick: selectAll },
    { key: 'failed', label: '仅失败', onClick: selectFailed },
    { key: 'clear', label: '取消选择', onClick: clearSelection },
  ]

  const actions: BatchAction[] = [
    {
      key: 'rerun',
      label: '重跑',
      onClick: () => setRerunDialogOpen(true),
    },
    {
      key: 'package',
      label: '打包',
      onClick: async () => {
        if (!workspaceId) return
        await batchPackage(workspaceId)
      },
    },
    {
      key: 'delete',
      label: '删除',
      danger: true,
      onClick: () => setDeleteDialogOpen(true),
    },
  ]

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
          <BatchToolbar
            selectedCount={selectedIds.size}
            filters={filters}
            actions={actions}
            onExitSelectMode={toggleSelectMode}
          />
          <BatchDeleteDialog
            open={deleteDialogOpen}
            count={selectedIds.size}
            onClose={() => setDeleteDialogOpen(false)}
            onConfirm={async () => {
              if (!workspaceId) return
              await batchDelete(workspaceId)
              setDeleteDialogOpen(false)
            }}
          />
          <BatchRerunDialog
            open={rerunDialogOpen}
            items={selectedJobs.map((j) => ({
              id: j.id,
              name: j.source_id || j.id,
            }))}
            phases={[]}
            itemLabel="任务"
            onConfirm={async () => {
              if (!workspaceId) return
              await batchRerun(workspaceId)
              setRerunDialogOpen(false)
            }}
            onClose={() => setRerunDialogOpen(false)}
          />
        </>
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
