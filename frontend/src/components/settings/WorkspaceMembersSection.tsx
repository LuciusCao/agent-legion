import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  listMembers,
  listUsers,
  putMember,
  removeMember,
} from '../../api/authApi'
import type { MemberResponse } from '../../api/authApi'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import settingsStyles from '../../pages/SettingsPage.module.css'
import styles from './WorkspaceMembersSection.module.css'

interface Props {
  workspaceId: string
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function WorkspaceMembersSection({ workspaceId }: Props) {
  const [selectedUserId, setSelectedUserId] = useState('')
  const [role, setRole] = useState<'editor' | 'viewer'>('editor')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const queryClient = useQueryClient()

  const { data: lists, error: listQueryError } = useQuery({
    queryKey: extraQueryKeys.workspaceMembers(workspaceId),
    queryFn: () => Promise.all([listMembers(workspaceId), listUsers()]),
  })
  const listError = toErrorMessage(listQueryError)
  const members = lists?.[0] ?? []
  const users = lists?.[1] ?? []

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.workspaceMembers(workspaceId),
    })
  }

  const memberIds = new Set(members.map((m) => m.id))
  const candidates = users.filter(
    (u) => !memberIds.has(u.id) && u.disabled_at === null
  )

  async function handleAdd() {
    if (!selectedUserId) return
    setError('')
    setLoading(true)
    try {
      await putMember(workspaceId, { user_id: selectedUserId, role })
      setSelectedUserId('')
      refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleRemove(member: MemberResponse) {
    if (!window.confirm(`确定要移除成员「${member.username}」吗？`)) return
    setError('')
    try {
      await removeMember(workspaceId, member.id)
      refresh()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <section id="workspace-members" className={settingsStyles.section}>
      <h2 className={settingsStyles.sectionTitle}>成员管理</h2>
      <hr className={settingsStyles.sectionDivider} />
      <div>
        {(error || listError) && (
          <p className={styles.error} role="alert">
            {error || listError}
          </p>
        )}

        <div className={styles.row}>
          <select
            className={styles.input}
            aria-label="选择用户"
            value={selectedUserId}
            onChange={(e) => setSelectedUserId(e.target.value)}
          >
            <option value="">选择要添加的用户</option>
            {candidates.map((u) => (
              <option key={u.id} value={u.id}>
                {u.username}
                {u.display_name ? `（${u.display_name}）` : ''}
              </option>
            ))}
          </select>
          <select
            className={styles.select}
            aria-label="成员角色"
            value={role}
            onChange={(e) => setRole(e.target.value as 'editor' | 'viewer')}
          >
            <option value="editor">可编辑</option>
            <option value="viewer">只读</option>
          </select>
          <button
            type="button"
            className={styles.button}
            onClick={() => void handleAdd()}
            disabled={loading || !selectedUserId}
          >
            添加成员
          </button>
        </div>

        {members.length === 0 ? (
          <p className={styles.empty}>暂无成员</p>
        ) : (
          <ul className={styles.list}>
            {members.map((member) => (
              <li
                key={member.id}
                className={styles.listItem}
                data-testid={`member-${member.id}`}
              >
                <span className={styles.itemLabel}>
                  {member.username}
                  {member.display_name ? `（${member.display_name}）` : ''}
                </span>
                <span className={styles.chip}>
                  {member.member_role === 'editor' ? '可编辑' : '只读'}
                </span>
                {member.disabled_at && (
                  <span className={`${styles.chip} ${styles.chipDisabled}`}>
                    已禁用
                  </span>
                )}
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => void handleRemove(member)}
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
