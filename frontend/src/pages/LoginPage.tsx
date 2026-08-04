import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, TextField } from '@mui/material'
import { useAuthStore } from '../stores/authStore'
import styles from './AuthPage.module.css'

function errorMessage(err: unknown): string {
  const status = (err as { status?: number } | null)?.status
  if (status === 401) return '用户名或密码错误'
  if (status === 429) return '失败次数过多，请稍后再试'
  return err instanceof Error ? err.message : String(err)
}

export default function LoginPage() {
  const navigate = useNavigate()
  const login = useAuthStore((s) => s.login)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!username.trim() || !password) return
    setError('')
    setSubmitting(true)
    try {
      await login(username.trim(), password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={(e) => void handleSubmit(e)}>
        <h1 className={styles.title}>登录 Agent Legion</h1>
        <TextField
          label="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          fullWidth
        />
        <TextField
          label="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
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
          登录
        </Button>
      </form>
    </div>
  )
}
