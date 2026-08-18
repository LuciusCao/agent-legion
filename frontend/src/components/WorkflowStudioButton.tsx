import { IconButton } from '@mui/material'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { MaterialIcon } from './MaterialIcon'

// Studio 全入口 admin-only（P4）：非 admin 不渲染 Workflow Studio 入口按钮，
// 路由侧由 WorkflowStudioPage 自守卫兜底（直接输入 URL 也会被重定向）。
export function WorkflowStudioButton() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const navigate = useNavigate()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  if (!isAdmin || !workspaceId) {
    return null
  }
  return (
    <IconButton
      size="small"
      aria-label="Workflow Studio"
      onClick={() => navigate(`/workspaces/${workspaceId}/workflow-studio`)}
    >
      <MaterialIcon name="account_tree" />
    </IconButton>
  )
}
