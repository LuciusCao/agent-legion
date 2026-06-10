import { useEffect, useMemo, useCallback, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { useJobStore } from '../stores/jobStore'
import { useDebouncedCallback } from '../hooks/useDebouncedCallback'
import { WorkspaceStatCards } from '../components/WorkspaceStatCards'
import { JobList } from '../components/JobList'
import { EmptyStateGuide } from '../components/EmptyStateGuide'
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
    selectedIds,
    statusFilter,
    setStatusFilter,
    searchQuery,
    setSearchQuery,
    selectAll,
    selectFailed,
    clearSelection,
    batchRerun,
    batchDelete,
    getFilteredJobs,
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

  const handleSelectAll = useCallback(() => {
    selectAll()
  }, [selectAll])

  const handleSelectFailed = useCallback(() => {
    selectFailed()
  }, [selectFailed])

  const handleBatchRerun = useCallback(async () => {
    if (!workspaceId || selectedIds.size === 0) return
    await batchRerun(workspaceId)
  }, [workspaceId, selectedIds, batchRerun])

  const handleBatchDelete = useCallback(async () => {
    if (!workspaceId || selectedIds.size === 0) return
    await batchDelete(workspaceId)
  }, [workspaceId, selectedIds, batchDelete])

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
      {selectedIds.size > 0 && (
        <div className={`${styles.batchToolbar} card-elevated`}>
          <span>已选择 {selectedIds.size} 项</span>
          <div className={styles.batchActions}>
            <md-text-button onClick={handleSelectAll}>全选</md-text-button>
            <md-text-button onClick={handleSelectFailed}>仅失败</md-text-button>
            <md-text-button onClick={clearSelection}>取消选择</md-text-button>
            <md-outlined-button onClick={handleBatchRerun}>
              批量重跑
            </md-outlined-button>
            <md-outlined-button
              style={{ color: 'var(--md-sys-color-error)' }}
              onClick={handleBatchDelete}
            >
              批量删除
            </md-outlined-button>
          </div>
        </div>
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
          <JobList workspaceId={workspaceId!} />
        </section>
      )}
    </div>
  )
}
