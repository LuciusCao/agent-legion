import { useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useWorkspaceStore } from '../stores/workspaceStore'

export default function WorkspaceMainPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const { fetchWorkspaceStats } = useWorkspaceStore()

  useEffect(() => {
    if (workspaceId) {
      fetchWorkspaceStats(workspaceId)
    }
  }, [workspaceId, fetchWorkspaceStats])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      <section
        className="stats-section"
        style={{
          padding: 16,
          borderRadius: 12,
          border: '1px solid var(--md-sys-color-outline-variant)',
          background: 'var(--md-sys-color-surface)',
        }}
      >
        统计栏占位
      </section>
      <section
        className="agents-section"
        style={{
          padding: 16,
          borderRadius: 12,
          border: '1px solid var(--md-sys-color-outline-variant)',
          background: 'var(--md-sys-color-surface)',
        }}
      >
        智能体状态占位
      </section>
      <section
        className="filter-section"
        style={{
          padding: 16,
          borderRadius: 12,
          border: '1px solid var(--md-sys-color-outline-variant)',
          background: 'var(--md-sys-color-surface)',
        }}
      >
        过滤 Chips 占位
      </section>
      <section
        className="list-section"
        style={{
          padding: 16,
          borderRadius: 12,
          border: '1px solid var(--md-sys-color-outline-variant)',
          background: 'var(--md-sys-color-surface)',
          flex: 1,
        }}
      >
        任务列表占位
      </section>
    </div>
  )
}
