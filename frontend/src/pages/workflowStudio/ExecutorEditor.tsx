import { useCallback, useEffect, useState } from 'react'
import { Button, MenuItem, TextField } from '@mui/material'
import {
  archiveExecutor,
  createExecutorDefinition,
  fetchExecutorDefinition,
  publishExecutor,
  saveExecutorDraft,
} from '../../api'
import type { ExecutorPayload } from '../../types'
import { useUiStore } from '../../stores/uiStore'
import { ExecutorVersionsDialog } from './ExecutorVersionsDialog'
import styles from './AgentsPanel.module.css'

// 当前仅注册了 code executor kind（EXEC-CODE-001）。
const kinds = ['code']

type Props = {
  /** null = 新建模式 */
  executorId: string | null
  onSaved: (executorId: string) => void
  onChanged: () => void
  onArchived: () => void
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

type ExecutorDefinitionShape = {
  kind?: string
  global_capacity?: number
  capabilities?: Record<string, Record<string, unknown>>
}

/**
 * Executor 定义编辑器。发布后的 definition 不可变：编辑已发布 Executor 就是
 * 保存一份新草稿再发布。capabilities 用 JSON 文本域编辑（path /
 * timeout_seconds / sandbox_network / config_schema 都在其中），非法 JSON
 * 在提交前拦截。发布只写 DB，重启服务后才影响调度。
 */
export function ExecutorEditor({
  executorId,
  onSaved,
  onChanged,
  onArchived,
}: Props) {
  const creating = executorId === null
  const [executorIdInput, setExecutorIdInput] = useState('')
  const [kind, setKind] = useState('code')
  const [globalCapacity, setGlobalCapacity] = useState(1)
  const [capabilitiesText, setCapabilitiesText] = useState('')
  const [hasDraft, setHasDraft] = useState(false)
  const [loading, setLoading] = useState(!creating)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [versionsOpen, setVersionsOpen] = useState(false)
  const showToast = useUiStore((s) => s.showToast)

  const load = useCallback(() => {
    if (creating) return Promise.resolve()
    return fetchExecutorDefinition(executorId)
      .then((detail) => {
        const draft = detail.latest?.status === 'draft' ? detail.latest : null
        const source = draft ?? detail.published ?? detail.latest
        setHasDraft(draft !== null)
        const definition = (source?.definition ?? {}) as ExecutorDefinitionShape
        setKind(definition.kind ?? 'code')
        setGlobalCapacity(definition.global_capacity ?? 1)
        setCapabilitiesText(
          definition.capabilities
            ? JSON.stringify(definition.capabilities, null, 2)
            : ''
        )
      })
      .catch((err) => {
        setError(errorMessage(err))
      })
  }, [executorId, creating])

  useEffect(() => {
    // The parent keys this component by executor id, so the load runs once
    // per mount and `loading` starts true via its useState initializer.
    if (creating) return
    let cancelled = false
    void load().finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [load, creating])

  function buildPayload(): ExecutorPayload | null {
    let capabilities: Record<string, Record<string, unknown>> | undefined
    const text = capabilitiesText.trim()
    if (text !== '') {
      try {
        const parsed: unknown = JSON.parse(text)
        if (
          typeof parsed !== 'object' ||
          parsed === null ||
          Array.isArray(parsed)
        )
          throw new Error('capabilities 必须是 JSON 对象')
        capabilities = parsed as Record<string, Record<string, unknown>>
      } catch (err) {
        setError(
          err instanceof Error && err.message.startsWith('capabilities')
            ? err.message
            : 'capabilities 不是合法 JSON'
        )
        return null
      }
    }
    return {
      kind,
      global_capacity: globalCapacity,
      ...(capabilities ? { capabilities } : {}),
    }
  }

  async function handleSaveDraft() {
    const payload = buildPayload()
    if (!payload) return
    setError('')
    setBusy(true)
    try {
      if (creating) {
        const newExecutorId = executorIdInput.trim()
        const created = await createExecutorDefinition({
          executor_id: newExecutorId,
          ...payload,
        })
        showToast(`Executor「${created.executor_id}」草稿已创建`, 'success')
        onSaved(created.executor_id)
      } else {
        await saveExecutorDraft(executorId, payload)
        setHasDraft(true)
        showToast('草稿已保存', 'success')
        onChanged()
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function handlePublish() {
    if (creating) return
    setError('')
    setBusy(true)
    try {
      await publishExecutor(executorId)
      setHasDraft(false)
      showToast('已发布（重启服务后生效）', 'success')
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleArchive() {
    if (creating) return
    if (!window.confirm(`确定要归档 Executor「${executorId}」吗？`)) return
    setError('')
    setBusy(true)
    try {
      await archiveExecutor(executorId)
      showToast('已归档', 'success')
      onArchived()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <p className={styles.hint}>加载中...</p>

  return (
    <div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <div className={styles.field}>
        <TextField
          label="Executor ID"
          variant="outlined"
          value={creating ? executorIdInput : executorId}
          onChange={(e) => setExecutorIdInput(e.target.value)}
          fullWidth
          slotProps={{ input: { readOnly: !creating } }}
          helperText={creating ? '创建后不可修改' : undefined}
        />
      </div>
      <div className={styles.field}>
        <TextField
          select
          label="Kind"
          variant="outlined"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          fullWidth
        >
          {kinds.map((k) => (
            <MenuItem key={k} value={k}>
              {k}
            </MenuItem>
          ))}
        </TextField>
      </div>
      <div className={styles.field}>
        <TextField
          label="Global Capacity"
          variant="outlined"
          type="number"
          value={globalCapacity}
          onChange={(e) => setGlobalCapacity(Number(e.target.value))}
          fullWidth
          slotProps={{ htmlInput: { min: 1 } }}
        />
      </div>
      <div className={styles.field}>
        <TextField
          label="capabilities（JSON，可空）"
          variant="outlined"
          value={capabilitiesText}
          onChange={(e) => setCapabilitiesText(e.target.value)}
          fullWidth
          multiline
          minRows={6}
          placeholder='{"clean_and_parse":{"path":"workflow_nodes/question_clean_parse.py"}}'
        />
      </div>
      <div className={styles.editorActions}>
        <Button
          variant="contained"
          onClick={() => void handleSaveDraft()}
          disabled={
            busy ||
            globalCapacity < 1 ||
            (creating && executorIdInput.trim() === '')
          }
        >
          {creating ? '创建草稿' : '保存草稿'}
        </Button>
        {!creating && (
          <Button
            variant="outlined"
            onClick={() => void handlePublish()}
            disabled={busy || !hasDraft}
          >
            发布
          </Button>
        )}
        {!creating && (
          <Button variant="outlined" onClick={() => setVersionsOpen(true)}>
            版本历史
          </Button>
        )}
        {!creating && (
          <Button
            color="error"
            variant="outlined"
            onClick={() => void handleArchive()}
            disabled={busy}
          >
            归档
          </Button>
        )}
      </div>
      {!creating && (
        <ExecutorVersionsDialog
          executorId={executorId}
          open={versionsOpen}
          onClose={() => setVersionsOpen(false)}
          onRolledBack={() => {
            setVersionsOpen(false)
            // 后端 rollback 直接落 published 新版本（无 draft）：重新拉取详情
            // 同步表单，并清掉 draft 标记。
            void load().then(() => setHasDraft(false))
            onChanged()
          }}
        />
      )}
    </div>
  )
}
