import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Typography } from '@mui/material'
import { useWorkspaces } from '../hooks/useWorkspaces'
import { useWorkspaceStats } from '../hooks/useWorkspaceStats'
import { useDashboardEvents } from '../hooks/useDashboardEvents'
import { useAuthStore } from '../stores/authStore'
import WorkspaceCard from '../components/WorkspaceCard'
import CreateWorkspaceDialog from '../components/CreateWorkspaceDialog'
import { UserMenu } from '../components/UserMenu'
import type { WorkspaceRecord } from '../types'

function DashboardWorkspaceCard({ workspace }: { workspace: WorkspaceRecord }) {
  const navigate = useNavigate()
  const { data: stats } = useWorkspaceStats(workspace.id)
  return (
    <WorkspaceCard
      name={workspace.name}
      workflowLabel={
        // 兜底 label 改读 workspace id（default_workflow_key 已 deprecated 且
        // v62 起恒等，#211 Phase 2；删除依赖列本身退役，属最后批次）。
        stats?.workflow_label || workspace.id
      }
      jobStats={stats?.job_stats || {}}
      codePool={stats?.code_pool}
      onClick={() => navigate(`/workspaces/${workspace.id}`)}
    />
  )
}

export function DashboardPage() {
  const { data: workspaces = [], isSuccess } = useWorkspaces()
  const [dialogOpen, setDialogOpen] = useState(false)
  // POST /api/workspaces 已 require_admin（P4）：非 admin 隐藏创建入口。
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')

  useDashboardEvents()

  return (
    <div style={{ padding: 24 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <h1 style={{ margin: 0, fontSize: 28 }}>Agent Legion</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <UserMenu />
          {isAdmin && (
            <Button variant="contained" onClick={() => setDialogOpen(true)}>
              新建 Workspace
            </Button>
          )}
        </div>
      </div>

      {/* #711：列表按成员关系过滤，未加入任何 workspace 的非 admin 拿到空列表。 */}
      {isSuccess && workspaces.length === 0 && !isAdmin && (
        <Typography color="text.secondary">
          你还没有加入任何 Workspace，请联系管理员添加。
        </Typography>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
          gap: 16,
        }}
      >
        {workspaces.map((w) => (
          <DashboardWorkspaceCard key={w.id} workspace={w} />
        ))}
      </div>

      <CreateWorkspaceDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </div>
  )
}
