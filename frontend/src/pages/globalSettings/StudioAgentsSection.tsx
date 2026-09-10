import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import { getStudioAgents, updateStudioAgents } from '../../api/studioAgents'
import type {
  StudioAgentRegistryResponse,
  StudioAgentRegistryUpdate,
} from '../../api/studioAgents'
import {
  availabilityBadge,
  ConflictRefreshDialog,
  DetectionCell,
  errorMessage,
  RedetectButton,
  serialize,
  toRows,
  useApplyRegistryResult,
} from './StudioAgentsSectionParts'
import type { AgentRow } from './StudioAgentsSectionParts'
import styles from '../GlobalSettingsPage.module.css'

// 对齐后端契约 StudioAgentRegistryEntry.id 的 pattern。
const ID_PATTERN = /^[a-z0-9][a-z0-9._-]*$/

function buildPayload(
  apiBase: string,
  rows: AgentRow[],
  revision: string
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
      // source 按契约随 payload 提交；服务端 PUT 时仍会重导（#332：未改动行
      // 保留原 source、编辑归 manual），这里带上当前值仅为满足类型。
      source: row.source ?? 'manual',
    }
  })
  return { api_base: apiBase.trim(), agents, revision }
}

function StudioAgentsEditor({
  initial,
}: {
  initial: StudioAgentRegistryResponse
}) {
  const [apiBase, setApiBase] = useState(initial.api_base)
  const [rows, setRows] = useState<AgentRow[]>(() => toRows(initial))
  const [availability, setAvailability] = useState<Record<string, boolean>>(
    initial.availability ?? {}
  )
  const [detection, setDetection] = useState(initial.detection ?? {})
  const [baseline, setBaseline] = useState(() =>
    serialize(initial.api_base, toRows(initial))
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // #355：快照版本随 GET 持有、随每次保存结果前进；409 冲突时由冲突响应刷新。
  const [revision, setRevision] = useState(initial.revision ?? '')
  // #355：409 冲突对话框打开态（不自动重试，由管理员选择刷新）。
  const [conflictOpen, setConflictOpen] = useState(false)
  // 409 响应携带的最新注册表（与存储同事务产出）——刷新动作的取数源。
  const [conflictBody, setConflictBody] =
    useState<StudioAgentRegistryResponse | null>(null)

  const isDirty = serialize(apiBase, rows) !== baseline

  function patchRow(index: number, patch: Partial<AgentRow>) {
    setRows((prev) =>
      prev.map((row, i) => (i === index ? { ...row, ...patch } : row))
    )
  }

  // 审核 P1：保存/重检测/409 刷新三路共用的「编辑器前进」原语（实现
  // 在 Parts——rows/baseline/availability/detection/revision 一次性
  // 对齐服务端文档，任何持有旧 revision 的状态都不得存活）。
  const applyResult = useApplyRegistryResult({
    setRows,
    setBaseline,
    setAvailability,
    setDetection,
    setRevision,
  })

  async function handleSave() {
    setError('')
    let payload: StudioAgentRegistryUpdate
    try {
      payload = buildPayload(apiBase, rows, revision)
    } catch (err) {
      setError(errorMessage(err))
      return
    }
    setSaving(true)
    try {
      const result = await updateStudioAgents(payload)
      applyResult(result)
      useUiStore.getState().showToast('Studio Agent 注册表已保存', 'success')
    } catch (err) {
      // #355：409 = 快照后有其他修改（典型为探测合并进新行）。静默覆盖会
      // 删掉这些行，改为弹确认对话框提供刷新（丢弃本地编辑、采用 409 携
      // 带的最新文档），不自动重试。
      if (
        err instanceof Error &&
        (err as Error & { status?: number }).status === 409
      ) {
        const body = (err as Error & { body?: unknown }).body as
          | StudioAgentRegistryResponse
          | undefined
        if (body && typeof body === 'object' && 'agents' in body) {
          setConflictBody(body)
          setConflictOpen(true)
        } else {
          setError(errorMessage(err))
        }
      } else {
        setError(errorMessage(err))
      }
    } finally {
      setSaving(false)
    }
  }

  function handleConflictRefresh() {
    // #355 审核 P1：编辑器状态只在挂载/保存/redetect 时前进——invalidate
    // 重取的数据不会被 useState 编辑器消费（refs 仍是旧快照），刷新后
    // 下一次保存必然再 409（死循环）。409 响应体就是服务端最新文档（与
    // 存储同事务产出），直接 applyResult 一次性前进 rows/baseline/
    // revision，本地未保存编辑被丢弃（对话框文案明示）。
    if (conflictBody) applyResult(conflictBody)
    setConflictBody(null)
    setConflictOpen(false)
  }

  return (
    <>
      <ConflictRefreshDialog
        open={conflictOpen}
        onContinue={() => setConflictOpen(false)}
        onRefresh={handleConflictRefresh}
      />
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <div className={styles.row}>
        <label className={styles.label} htmlFor="studio-agents-api-base">
          平台回调地址（api_base）
        </label>
        <input
          id="studio-agents-api-base"
          className={styles.input}
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
        />
      </div>
      <p className={styles.hint}>
        agent 启动后通过该地址回呼平台获取工具（会话上下文、材料读写等）。 agent
        与服务端同机时保持默认值即可；仅当 agent 运行在其他机器或容器时， 改为
        agent 可达的平台地址。该地址会收到仅本次会话有效的临时 token，
        指向外部网络前请确认安全。
      </p>
      <table className={`${styles.table} ${styles.tableBreak}`}>
        <thead>
          <tr>
            <th>id</th>
            <th>label</th>
            <th>command</th>
            <th>args</th>
            <th>PATH 探测</th>
            <th>来源 / 检测</th>
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
                <DetectionCell
                  source={row.source}
                  status={detection[row.id.trim()]}
                />
              </td>
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
        <RedetectButton
          disabled={isDirty || saving}
          onDone={applyResult}
          onError={setError}
        />
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
        在这里管理 Studio 对话可启动的 AI agent（需支持 ACP 协议，如 Claude
        Code、Codex、Kimi）。点击「重新检测」会自动发现服务器上已安装的
        agent；也可以手动添加，手动条目不会被检测覆盖。
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
