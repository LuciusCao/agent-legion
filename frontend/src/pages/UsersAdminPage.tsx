import { useState } from 'react'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { useAuthStore } from '../stores/authStore'
import { createUser, listUsers, updateUser } from '../api/authApi'
import type { UserResponse } from '../api/authApi'
import { useAsync } from '../hooks/useAsync'
import styles from './UsersAdminPage.module.css'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function UsersAdminPage() {
  const currentUser = useAuthStore((s) => s.user)
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'admin' | 'member'>('member')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  const isAdmin = currentUser?.role === 'admin'

  const { data: userList, error: listError } = useAsync(
    () => listUsers(),
    [isAdmin, refreshKey],
    { enabled: isAdmin }
  )
  const users = userList ?? []

  function refresh() {
    setRefreshKey((key) => key + 1)
  }

  async function handleCreate() {
    const trimmed = username.trim()
    if (!trimmed || !password) return
    setError('')
    setLoading(true)
    try {
      await createUser({
        username: trimmed,
        password,
        display_name: displayName.trim(),
        role,
      })
      setUsername('')
      setDisplayName('')
      setPassword('')
      setRole('member')
      refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleToggleDisabled(user: UserResponse) {
    setError('')
    try {
      await updateUser(user.id, { disabled: user.disabled_at === null })
      refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function handleToggleRole(user: UserResponse) {
    setError('')
    try {
      await updateUser(user.id, {
        role: user.role === 'admin' ? 'member' : 'admin',
      })
      refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  async function handleResetPassword(user: UserResponse) {
    const next = window.prompt(`为用户「${user.username}」设置新密码`)
    if (!next) return
    setError('')
    try {
      await updateUser(user.id, { password: next })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  if (!isAdmin) {
    return (
      <AppShell
        appBar={({ scrolled }) => (
          <AppBar title="用户管理" backTo="/" scrolled={scrolled} />
        )}
      >
        <div className={styles.main}>
          <p className={styles.empty}>无权限访问，仅管理员可管理用户。</p>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar title="用户管理" backTo="/" scrolled={scrolled} />
      )}
    >
      <div className={styles.main}>
        {(error || listError) && (
          <p className={styles.error} role="alert">
            {error || listError}
          </p>
        )}

        <div className={styles.card}>
          <h3 className={styles.heading}>创建用户</h3>
          <div className={styles.row}>
            <input
              className={styles.input}
              placeholder="用户名"
              aria-label="用户名"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <input
              className={styles.input}
              placeholder="显示名（可空）"
              aria-label="显示名"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
            <input
              className={styles.input}
              placeholder="密码"
              aria-label="密码"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <select
              className={styles.select}
              aria-label="角色"
              value={role}
              onChange={(e) => setRole(e.target.value as 'admin' | 'member')}
            >
              <option value="member">成员</option>
              <option value="admin">管理员</option>
            </select>
            <button
              type="button"
              className={styles.button}
              onClick={() => void handleCreate()}
              disabled={loading || !username.trim() || !password}
            >
              创建
            </button>
          </div>
        </div>

        {users.length === 0 ? (
          <p className={styles.empty}>暂无用户</p>
        ) : (
          <ul className={styles.list}>
            {users.map((user) => (
              <li
                key={user.id}
                className={styles.listItem}
                data-testid={`user-${user.id}`}
              >
                <span className={styles.itemLabel}>
                  {user.username}
                  {user.display_name ? `（${user.display_name}）` : ''}
                </span>
                <span className={styles.chip}>
                  {user.role === 'admin' ? '管理员' : '成员'}
                </span>
                <span
                  className={`${styles.chip} ${
                    user.disabled_at ? styles.chipDisabled : styles.chipActive
                  }`}
                >
                  {user.disabled_at ? '已禁用' : '正常'}
                </span>
                <button
                  type="button"
                  className={styles.textButton}
                  onClick={() => void handleToggleRole(user)}
                >
                  {user.role === 'admin' ? '设为成员' : '设为管理员'}
                </button>
                <button
                  type="button"
                  className={styles.textButton}
                  onClick={() => void handleResetPassword(user)}
                >
                  重置密码
                </button>
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => void handleToggleDisabled(user)}
                >
                  {user.disabled_at ? '启用' : '禁用'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  )
}
