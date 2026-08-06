import { Button } from '@mui/material'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

// Dashboard header: admin-only entries (含全局监控面板) plus logout.
const ADMIN_LINKS = [
  { label: '用户管理', to: '/admin/users' },
  { label: '设置', to: '/admin/settings' },
  { label: '监控面板', to: '/monitoring' }, // 全局监控仅 admin 可见
]

export function UserMenu() {
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  return (
    <>
      {user?.role === 'admin' &&
        ADMIN_LINKS.map((link) => (
          <Button
            key={link.to}
            variant="outlined"
            onClick={() => navigate(link.to)}
          >
            {link.label}
          </Button>
        ))}
      <Button variant="text" onClick={() => void logout()}>
        退出登录
      </Button>
    </>
  )
}
