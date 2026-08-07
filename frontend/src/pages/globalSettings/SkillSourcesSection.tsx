import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import {
  getSkillSources,
  relockSkillSources,
  updateSkillSource,
} from '../../api/skillSources'
import type { SkillSourceEntry } from '../../api/skillSources'
import styles from '../GlobalSettingsPage.module.css'

interface EditingState {
  key: string
  repo: string
  ref: string
}

function shortCommit(commit: string | null): string {
  return commit ? commit.slice(0, 8) : '—'
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function SkillSourcesTable({ skills }: { skills: SkillSourceEntry[] }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<EditingState | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function handleSave() {
    if (!editing) return
    setError('')
    setSaving(true)
    try {
      const result = await updateSkillSource(editing.key, {
        repo: editing.repo,
        ref: editing.ref,
      })
      queryClient.setQueryData(extraQueryKeys.skillSources(), result)
      setEditing(null)
      useUiStore.getState().showToast('skill 源已保存，刷新锁后生效', 'success')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <table className={styles.table}>
        <thead>
          <tr>
            <th>skill</th>
            <th>repo</th>
            <th>ref</th>
            <th>locked commit</th>
            <th>状态</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {skills.map((skill) => (
            <tr key={skill.key}>
              <td>{skill.key}</td>
              {editing?.key === skill.key ? (
                <>
                  <td>
                    <input
                      className={styles.input}
                      aria-label={`${skill.key} repo`}
                      value={editing.repo}
                      onChange={(e) =>
                        setEditing({ ...editing, repo: e.target.value })
                      }
                    />
                  </td>
                  <td>
                    <input
                      className={styles.currencyInput}
                      aria-label={`${skill.key} ref`}
                      value={editing.ref}
                      onChange={(e) =>
                        setEditing({ ...editing, ref: e.target.value })
                      }
                    />
                  </td>
                </>
              ) : (
                <>
                  <td>{skill.repo}</td>
                  <td>{skill.ref}</td>
                </>
              )}
              <td>{shortCommit(skill.locked_commit)}</td>
              <td>
                {skill.stale && (
                  <span className={styles.staleBadge}>stale</span>
                )}
              </td>
              <td>
                {editing?.key === skill.key ? (
                  <>
                    <button
                      type="button"
                      className={styles.textButton}
                      disabled={saving}
                      onClick={() => void handleSave()}
                    >
                      {saving ? '保存中…' : '保存'}
                    </button>{' '}
                    <button
                      type="button"
                      className={styles.textButton}
                      disabled={saving}
                      onClick={() => setEditing(null)}
                    >
                      取消
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className={styles.textButton}
                    aria-label={`编辑 ${skill.key}`}
                    onClick={() =>
                      setEditing({
                        key: skill.key,
                        repo: skill.repo,
                        ref: skill.ref,
                      })
                    }
                  >
                    编辑
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export function SkillSourcesSection() {
  const queryClient = useQueryClient()
  const { data, error: loadQueryError } = useQuery({
    queryKey: extraQueryKeys.skillSources(),
    queryFn: getSkillSources,
  })
  const [relocking, setRelocking] = useState(false)
  const [error, setError] = useState('')
  const loadError = toErrorMessage(loadQueryError)

  async function handleRelock() {
    setError('')
    setRelocking(true)
    try {
      const result = await relockSkillSources()
      queryClient.setQueryData(extraQueryKeys.skillSources(), result)
      useUiStore.getState().showToast('skill 锁已刷新', 'success')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setRelocking(false)
    }
  }

  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>Skill 源管理</h3>
      <p className={styles.hint}>
        skill 源与锁存于数据库；修改 ref 后需刷新锁解析 commit。
      </p>
      {(error || loadError) && (
        <p className={styles.error} role="alert">
          {error || loadError}
        </p>
      )}
      <button
        type="button"
        className={styles.textButton}
        disabled={relocking}
        onClick={() => void handleRelock()}
      >
        {relocking ? '刷新中…' : '刷新锁'}
      </button>
      {data && <SkillSourcesTable skills={data.skills} />}
    </div>
  )
}
