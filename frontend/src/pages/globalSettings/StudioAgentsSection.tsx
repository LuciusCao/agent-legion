import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import { getStudioAgents, updateStudioAgents } from '../../api/studioAgents'
import type {
  StudioAgentRegistryResponse,
  StudioAgentRegistryUpdate,
} from '../../api/studioAgents'
import styles from '../GlobalSettingsPage.module.css'

// 对齐后端契约 StudioAgentRegistryEntry.id 的 pattern。
const ID_PATTERN = /^[a-z0-9][a-z0-9._-]*$/

interface AgentRow {
  id: string
  label: string
  command: string
  argsText: string
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function toRows(document: StudioAgentRegistryResponse): AgentRow[] {
  return (document.agents ?? []).map((agent) => ({
    id: agent.id,
    label: agent.label,
    command: agent.command,
    argsText: (agent.args ?? []).join(' '),
  }))
}

function serialize(apiBase: string, rows: AgentRow[]): string {
  return JSON.stringify({ apiBase: apiBase.trim(), rows })
}

function buildPayload(
  apiBase: string,
  rows: AgentRow[]
): StudioAgentRegistryUpdate {
  if (!apiBase.trim()) {
    throw new Error('api_base 不能为空')
  }
  const ids = new Set<string>()
  const agents = rows.map((row, index) => {
    const id = row.id.trim()
    const label = row.label.trim()
    const command = row.command.trim()
    if (!ID_PATTERN.test(id)) {
      throw new Error(
        `第 ${index + 1} 个 agent 的 id 不合法：必须匹配 ^[a-z0-9][a-z0-9._-]*$`
      )
    }
    if (!label) {
      throw new Error(`第 ${index + 1} 个 agent 的 label 不能为空`)
    }
    if (!command) {
      throw new Error(`第 ${index + 1} 个 agent 的 command 不能为空`)
    }
    if (ids.has(id)) {
      throw new Error(`agent id 重复：${id}`)
    }
    ids.add(id)
    return {
      id,
      label,
      command,
      args: row.argsText.split(/\s+/).filter(Boolean),
    }
  })
  return { api_base: apiBase.trim(), agents }
}

function availabilityBadge(availability: Record<string, boolean>, id: string) {
  const value = availability[id.trim()]
  if (value === undefined) return '—'
  return value ? '可用' : <span className={styles.staleBadge}>不可用</span>
}

function StudioAgentsEditor({
  initial,
}: {
  initial: StudioAgentRegistryResponse
}) {
  const queryClient = useQueryClient()
  const [apiBase, setApiBase] = useState(initial.api_base)
  const [rows, setRows] = useState<AgentRow[]>(() => toRows(initial))
  const [availability, setAvailability] = useState<Record<string, boolean>>(
    initial.availability ?? {}
  )
  const [baseline, setBaseline] = useState(() =>
    serialize(initial.api_base, toRows(initial))
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const isDirty = serialize(apiBase, rows) !== baseline

  function patchRow(index: number, patch: Partial<AgentRow>) {
    setRows((prev) =>
      prev.map((row, i) => (i === index ? { ...row, ...patch } : row))
    )
  }

  async function handleSave() {
    setError('')
    let payload: StudioAgentRegistryUpdate
    try {
      payload = buildPayload(apiBase, rows)
    } catch (err) {
      setError(errorMessage(err))
      return
    }
    setSaving(true)
    try {
      const result = await updateStudioAgents(payload)
      queryClient.setQueryData(extraQueryKeys.studioAgents(), result)
      setBaseline(serialize(result.api_base, toRows(result)))
      setAvailability(result.availability ?? {})
      useUiStore.getState().showToast('Studio Agent 注册表已保存', 'success')
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
      <div className={styles.row}>
        <label className={styles.label} htmlFor="studio-agents-api-base">
          api_base
        </label>
        <input
          id="studio-agents-api-base"
          className={styles.input}
          aria-label="api_base"
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
        />
      </div>
      <table className={`${styles.table} ${styles.tableBreak}`}>
        <thead>
          <tr>
            <th>id</th>
            <th>label</th>
            <th>command</th>
            <th>args</th>
            <th>PATH 探测</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} data-testid={`studio-agent-row-${index}`}>
              <td>
                <input
                  className={styles.input}
                  aria-label={`agent-id-${index}`}
                  value={row.id}
                  onChange={(e) => patchRow(index, { id: e.target.value })}
                />
              </td>
              <td>
                <input
                  className={styles.input}
                  aria-label={`agent-label-${index}`}
                  value={row.label}
                  onChange={(e) => patchRow(index, { label: e.target.value })}
                />
              </td>
              <td>
                <input
                  className={styles.input}
                  aria-label={`agent-command-${index}`}
                  value={row.command}
                  onChange={(e) => patchRow(index, { command: e.target.value })}
                />
              </td>
              <td>
                <input
                  className={styles.input}
                  aria-label={`agent-args-${index}`}
                  value={row.argsText}
                  onChange={(e) =>
                    patchRow(index, { argsText: e.target.value })
                  }
                />
              </td>
              <td>{availabilityBadge(availability, row.id)}</td>
              <td>
                <button
                  type="button"
                  className={styles.dangerButton}
                  aria-label={`删除 agent ${index}`}
                  onClick={() =>
                    setRows((prev) => prev.filter((_, i) => i !== index))
                  }
                >
                  删除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={styles.row}>
        <button
          type="button"
          className={styles.textButton}
          onClick={() =>
            setRows((prev) => [
              ...prev,
              { id: '', label: '', command: '', argsText: '' },
            ])
          }
        >
          添加 agent
        </button>
        <button
          type="button"
          className={styles.textButton}
          disabled={!isDirty || saving}
          onClick={() => void handleSave()}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </>
  )
}

export function StudioAgentsSection() {
  const { data, error: loadQueryError } = useQuery({
    queryKey: extraQueryKeys.studioAgents(),
    queryFn: getStudioAgents,
  })
  const loadError = toErrorMessage(loadQueryError)

  return (
    <div className={styles.card}>
      <h3 className={styles.heading}>Studio Agent 管理</h3>
      <p className={styles.hint}>
        Studio chat 的 ACP agent 注册表，整文档保存；可用性以后端 PATH
        探测为准（约 1 分钟缓存）。args 以空格分隔。
      </p>
      {loadError && (
        <p className={styles.error} role="alert">
          {loadError}
        </p>
      )}
      {data && <StudioAgentsEditor initial={data} />}
    </div>
  )
}
