import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, TextField } from '@mui/material'
import { useAuthStore } from '../stores/authStore'
import styles from './AuthPage.module.css'

export default function SetupPage() {
  const navigate = useNavigate()
  const bootstrap = useAuthStore((s) => s.bootstrap)
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!username.trim() || !password) return
    if (password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    setError('')
    setSubmitting(true)
    try {
      await bootstrap(username.trim(), password, displayName.trim())
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={(e) => void handleSubmit(e)}>
        <h1 className={styles.title}>初始化管理员</h1>
        <p className={styles.hint}>
          系统还没有任何用户，请创建首个管理员账号。
        </p>
        <TextField
          label="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          fullWidth
        />
        <TextField
          label="显示名（可选）"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          fullWidth
        />
        <TextField
          label="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          fullWidth
        />
        <TextField
          label="确认密码"
          type="password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          autoComplete="new-password"
          fullWidth
        />
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <Button
          type="submit"
          variant="contained"
          disabled={submitting || !username.trim() || !password}
        >
          创建并登录
        </Button>
      </form>
    </div>
  )
}
