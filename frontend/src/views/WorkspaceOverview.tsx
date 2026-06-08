import { useWorkspaceStore } from '../stores/workspaceStore'

type Props = {
  isVideoHive: boolean
}

export default function WorkspaceOverview({ isVideoHive }: Props) {
  const { currentWorkspace, workspaceStats } = useWorkspaceStore()

  if (isVideoHive) {
    return (
      <div>
        <h3>Video Hive 概览</h3>
        <p style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
          视频处理流水线的统计信息将在此展示。
        </p>
      </div>
    )
  }

  const stats = currentWorkspace ? workspaceStats[currentWorkspace.id] : null

  return (
    <div>
      <h3>{currentWorkspace?.name || 'Workspace'} 概览</h3>
      {stats && Object.keys(stats.job_stats).length > 0 ? (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: 16,
            marginTop: 16,
          }}
        >
          {Object.entries(stats.job_stats).map(([status, count]) => (
            <div
              key={status}
              style={{
                padding: 16,
                borderRadius: 12,
                background: 'var(--md-sys-color-surface-container)',
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--md-sys-color-on-surface-variant)',
                }}
              >
                {status}
              </div>
              <div style={{ fontSize: 24, fontWeight: 600 }}>{count}</div>
            </div>
          ))}
        </div>
      ) : (
        <p
          style={{
            color: 'var(--md-sys-color-on-surface-variant)',
            marginTop: 16,
          }}
        >
          暂无统计信息
        </p>
      )}
    </div>
  )
}
