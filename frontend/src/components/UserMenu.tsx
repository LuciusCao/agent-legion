import { Button } from '@mui/material'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

// Dashboard header actions tied to the session: admin user management entry
// plus logout.
export function UserMenu() {
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  return (
    <>
      {user?.role === 'admin' && (
        <Button variant="outlined" onClick={() => navigate('/admin/users')}>
          用户管理
        </Button>
      )}
      <Button variant="text" onClick={() => void logout()}>
        退出登录
      </Button>
    </>
  )
}
