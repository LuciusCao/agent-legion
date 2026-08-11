import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import {
  createConnection,
  deleteConnection,
  getConnections,
  getConnectionTypes,
  testConnection,
  updateConnection,
} from '../../api/connections'
import type { ConnectionTypeView, ConnectionView } from '../../api/connections'
import styles from '../GlobalSettingsPage.module.css'
import localStyles from './ConnectionsSection.module.css'

// 与后端 server/app/services/connections.py 的 _KEY_PATTERN 保持一致。
const KEY_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function formatTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function tokenStatusLabel(connection: ConnectionView): string {
  const token = connection.token
  if (!token || (!token.expires_at && !token.refreshed_at)) return '未获取'
  const parts: string[] = []
  if (token.expires_at) parts.push(`有效期至 ${formatTime(token.expires_at)}`)
  if (token.refreshed_at)
    parts.push(`上次刷新 ${formatTime(token.refreshed_at)}`)
  return parts.join(' · ')
}

/** 解析 JSON textarea 内容；非对象或非法 JSON 返回错误消息。 */
function parseConfigJson(
  text: string
):
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; message: string } {
  try {
    const value: unknown = JSON.parse(text)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return { ok: false, message: '配置必须是 JSON 对象' }
    }
    return { ok: true, value: value as Record<string, unknown> }
  } catch {
    return { ok: false, message: 'JSON 格式非法，请检查后再保存' }
  }
}

function SecretHint({ type }: { type: ConnectionTypeView | undefined }) {
  if (!type || type.secret_keys.length === 0) return null
  return (
    <p className={styles.hint}>
      secret 键：{type.secret_keys.join('、')}。在 JSON
      中直接写明文，保存时服务端会抽走并加密存储；回显为{' '}
      {'{"secret_set": true}'}
      ，保持不变即不修改，提交新字符串覆盖，提交空字符串清除。
    </p>
  )
}

interface ConfigFormProps {
  title: string
  displayName: string
  jsonText: string
  saving: boolean
  submitLabel: string
  typeView: ConnectionTypeView | undefined
  onDisplayNameChange: (value: string) => void
  onJsonChange: (value: string) => void
  onSubmit: () => void
  onCancel: () => void
  extraFields?: React.ReactNode
  submitDisabled?: boolean
}

function ConfigForm({
  title,
  displayName,
  jsonText,
  saving,
  submitLabel,
  typeView,
  onDisplayNameChange,
  onJsonChange,
  onSubmit,
  onCancel,
  extraFields,
  submitDisabled,
}: ConfigFormProps) {
  const parsed = parseConfigJson(jsonText)
  return (
    <div className={localStyles.subCard}>
      <p className={styles.groupTitle}>{title}</p>
      {extraFields}
      <div className={localStyles.formRow}>
        <label
          className={localStyles.formLabel}
          htmlFor="connection-display-name"
        >
          显示名
        </label>
        <input
          id="connection-display-name"
          className={styles.input}
          value={displayName}
          onChange={(e) => onDisplayNameChange(e.target.value)}
        />
      </div>
      <div className={localStyles.formRow}>
        <label
          className={localStyles.formLabel}
          htmlFor="connection-config-json"
        >
          配置 JSON
        </label>
        <textarea
          id="connection-config-json"
          className={localStyles.jsonTextarea}
          value={jsonText}
          onChange={(e) => onJsonChange(e.target.value)}
          spellCheck={false}
        />
        {!parsed.ok && (
          <p className={styles.error} role="alert">
            {parsed.message}
          </p>
        )}
        <SecretHint type={typeView} />
      </div>
      <button
        type="button"
        className={styles.textButton}
        disabled={saving || !parsed.ok || submitDisabled}
        onClick={onSubmit}
      >
        {saving ? '保存中…' : submitLabel}
      </button>{' '}
      <button
        type="button"
        className={styles.textButton}
        disabled={saving}
        onClick={onCancel}
      >
        取消
      </button>
    </div>
  )
}

interface RowProps {
  connection: ConnectionView
  onEdit: (connection: ConnectionView) => void
}

function ConnectionRow({ connection, onEdit }: RowProps) {
  const queryClient = useQueryClient()
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{
    ok: boolean
    message: string
  } | null>(null)

  async function invalidate() {
    await queryClient.invalidateQueries({
      queryKey: extraQueryKeys.connections(),
    })
  }

  async function handleToggleEnabled(enabled: boolean) {
    try {
      await updateConnection(connection.key, { enabled })
      await invalidate()
      useUiStore
        .getState()
        .showToast(enabled ? '连接已启用' : '连接已停用', 'success')
    } catch (err) {
      useUiStore.getState().showToast(errorMessage(err), 'error')
    }
  }

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await testConnection(connection.key)
      setTestResult({ ok: result.ok, message: result.message })
      useUiStore
        .getState()
        .showToast(
          result.ok ? '连接测试成功' : `连接测试失败：${result.message}`,
          result.ok ? 'success' : 'error'
        )
    } catch (err) {
      const message = errorMessage(err)
      setTestResult({ ok: false, message })
      useUiStore.getState().showToast(`连接测试失败：${message}`, 'error')
    } finally {
      setTesting(false)
    }
  }

  async function handleDelete() {
    if (!window.confirm(`确定删除连接 ${connection.key}？`)) return
    try {
      await deleteConnection(connection.key)
      await invalidate()
      useUiStore.getState().showToast('连接已删除', 'success')
    } catch (err) {
      useUiStore.getState().showToast(errorMessage(err), 'error')
    }
  }

  return (
    <tr>
      <td>{connection.key}</td>
      <td>{connection.type}</td>
      <td>{connection.display_name}</td>
      <td>
        <input
          type="checkbox"
          aria-label={`启用 ${connection.key}`}
          checked={connection.enabled}
          onChange={(e) => void handleToggleEnabled(e.target.checked)}
        />
      </td>
      <td className={localStyles.tokenCell}>
        {tokenStatusLabel(connection)}
        {testResult && (
          <p
            className={
              testResult.ok ? localStyles.okText : localStyles.failText
            }
          >
            {testResult.ok
              ? `测试成功：${testResult.message}`
              : `测试失败：${testResult.message}`}
          </p>
        )}
      </td>
      <td>
        <button
          type="button"
          className={styles.textButton}
          aria-label={`编辑 ${connection.key}`}
          onClick={() => onEdit(connection)}
        >
          编辑
        </button>{' '}
        <button
          type="button"
          className={styles.textButton}
          aria-label={`测试 ${connection.key}`}
          disabled={testing}
          onClick={() => void handleTest()}
        >
          {testing ? '测试中…' : '测试连接'}
        </button>{' '}
        <button
          type="button"
          className={styles.dangerButton}
          aria-label={`删除 ${connection.key}`}
          onClick={() => void handleDelete()}
        >
          删除
        </button>
      </td>
    </tr>
  )
}

interface EditingState {
  key: string
  displayName: string
  jsonText: string
  /** 打开编辑时的 jsonText，用于判断 config 是否真的被改动。 */
  initialJsonText: string
}

interface CreatingState {
  type: string
  key: string
  displayName: string
  jsonText: string
}

export function ConnectionsSection() {
  const queryClient = useQueryClient()
  const { data, error: loadQueryError } = useQuery({
    queryKey: extraQueryKeys.connections(),
    queryFn: getConnections,
  })
  const { data: typesData } = useQuery({
    queryKey: extraQueryKeys.connectionTypes(),
    queryFn: getConnectionTypes,
  })
  const [editing, setEditing] = useState<EditingState | null>(null)
  const [creating, setCreating] = useState<CreatingState | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const loadError = toErrorMessage(loadQueryError)

  const types = typesData?.types ?? []
  const typeOf = (type: string) => types.find((t) => t.type === type)

  async function applyMutation(action: () => Promise<unknown>, toast: string) {
    setError('')
    setSaving(true)
    try {
      await action()
      await queryClient.invalidateQueries({
        queryKey: extraQueryKeys.connections(),
      })
      setEditing(null)
      setCreating(null)
      useUiStore.getState().showToast(toast, 'success')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveEdit() {
    if (!editing) return
    // jsonText 未改动时只发 display_name：后端只要 config 非 None 就会清
    // connection_tokens 缓存，仅改显示名不应波及 token。
    const configChanged = editing.jsonText !== editing.initialJsonText
    let config: Record<string, unknown> | undefined
    if (configChanged) {
      const parsed = parseConfigJson(editing.jsonText)
      if (!parsed.ok) {
        setError(parsed.message)
        return
      }
      config = parsed.value
    }
    await applyMutation(
      () =>
        updateConnection(editing.key, {
          display_name: editing.displayName,
          ...(configChanged ? { config } : {}),
        }),
      '连接已保存'
    )
  }

  async function handleCreate() {
    if (!creating) return
    const parsed = parseConfigJson(creating.jsonText)
    if (!parsed.ok) {
      setError(parsed.message)
      return
    }
    await applyMutation(
      () =>
        createConnection({
          key: creating.key,
          type: creating.type,
          display_name: creating.displayName,
          config: parsed.value,
        }),
      '连接已创建'
    )
  }

  const createKeyValid = creating !== null && KEY_PATTERN.test(creating.key)

  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>外部服务连接</h3>
      <p className={styles.hint}>
        实例级外部服务凭据集中管理；secret 值加密存储，节点配置通过连接 key
        引用。
      </p>
      {(error || loadError) && (
        <p className={styles.error} role="alert">
          {error || loadError}
        </p>
      )}
      {data && data.connections.length === 0 && (
        <p className={styles.empty}>暂无连接</p>
      )}
      {data && data.connections.length > 0 && (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>key</th>
              <th>类型</th>
              <th>显示名</th>
              <th>启用</th>
              <th>token 状态</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.connections.map((connection) => (
              <ConnectionRow
                key={connection.key}
                connection={connection}
                onEdit={(c) => {
                  // 编辑与新建表单互斥：两者同时打开会出现重复的 DOM id
                  // （connection-display-name 等）并共享 saving 状态互扰。
                  setCreating(null)
                  setEditing({
                    key: c.key,
                    displayName: c.display_name,
                    jsonText: JSON.stringify(c.config, null, 2),
                    initialJsonText: JSON.stringify(c.config, null, 2),
                  })
                }}
              />
            ))}
          </tbody>
        </table>
      )}
      {editing && (
        <ConfigForm
          title={`编辑连接 ${editing.key}`}
          displayName={editing.displayName}
          jsonText={editing.jsonText}
          saving={saving}
          submitLabel="保存"
          typeView={typeOf(
            data?.connections.find((c) => c.key === editing.key)?.type ?? ''
          )}
          onDisplayNameChange={(v) =>
            setEditing({ ...editing, displayName: v })
          }
          onJsonChange={(v) => setEditing({ ...editing, jsonText: v })}
          onSubmit={() => void handleSaveEdit()}
          onCancel={() => {
            setEditing(null)
            setError('')
          }}
        />
      )}
      {creating ? (
        <ConfigForm
          title="新建连接"
          displayName={creating.displayName}
          jsonText={creating.jsonText}
          saving={saving}
          submitLabel="创建"
          typeView={typeOf(creating.type)}
          submitDisabled={
            !createKeyValid || !creating.type || !creating.displayName
          }
          onDisplayNameChange={(v) =>
            setCreating({ ...creating, displayName: v })
          }
          onJsonChange={(v) => setCreating({ ...creating, jsonText: v })}
          onSubmit={() => void handleCreate()}
          onCancel={() => {
            setCreating(null)
            setError('')
          }}
          extraFields={
            <>
              <div className={localStyles.formRow}>
                <label
                  className={localStyles.formLabel}
                  htmlFor="connection-type"
                >
                  类型
                </label>
                <select
                  id="connection-type"
                  className={styles.input}
                  value={creating.type}
                  onChange={(e) =>
                    setCreating({ ...creating, type: e.target.value })
                  }
                >
                  {types.map((t) => (
                    <option key={t.type} value={t.type}>
                      {t.type} — {t.description}
                    </option>
                  ))}
                </select>
                {typeOf(creating.type) &&
                  typeOf(creating.type)!.required_config_keys.length > 0 && (
                    <p className={styles.hint}>
                      必填配置键：
                      {typeOf(creating.type)!.required_config_keys.join('、')}
                    </p>
                  )}
              </div>
              <div className={localStyles.formRow}>
                <label
                  className={localStyles.formLabel}
                  htmlFor="connection-key"
                >
                  连接 key（小写字母 / 数字 / 连字符）
                </label>
                <input
                  id="connection-key"
                  className={styles.input}
                  value={creating.key}
                  onChange={(e) =>
                    setCreating({ ...creating, key: e.target.value })
                  }
                />
                {creating.key && !createKeyValid && (
                  <p className={styles.error} role="alert">
                    key 只能包含小写字母、数字与连字符
                  </p>
                )}
              </div>
            </>
          }
        />
      ) : (
        <button
          type="button"
          className={styles.textButton}
          onClick={() => {
            // 与编辑表单互斥（见 onEdit）。
            setEditing(null)
            setCreating({
              type: types[0]?.type ?? '',
              key: '',
              displayName: '',
              jsonText: '{}',
            })
          }}
        >
          新建连接
        </button>
      )}
    </div>
  )
}
