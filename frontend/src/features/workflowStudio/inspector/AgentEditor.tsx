import { useCallback, useEffect, useState } from 'react'
import { Button, MenuItem, TextField } from '@mui/material'
import {
  archiveAgent,
  createAgentDefinition,
  fetchAgentDefinition,
  publishAgent,
  saveAgentDraft,
} from '../../../api'
import type { AgentDefinitionPayload, AgentRuntime } from '../../../types'
import { useUiStore } from '../../../stores/uiStore'
import { AgentVersionsDialog } from './AgentVersionsDialog'
import styles from './AgentsPanel.module.css'

// #408：velites（自研 harness，流式事件 + token 计量）是默认且优先级
// 更高的 runtime，排在选项首位；pi 是外部 runtime，仅作备选。
const runtimes: AgentRuntime[] = ['velites', 'pi']
const toolOptions = ['read', 'write', 'bash']

type Props = {
  /** 当前 workspace（Agent 定义为 workspace 作用域，schema v46） */
  workspaceId: string
  /** null = 新建模式 */
  agentId: string | null
  /** 新建模式下预填的 capability（节点详情内嵌新建时传入） */
  initialCapability?: string
  onSaved: (agentId: string) => void
  onChanged: () => void
  onArchived: () => void
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

// api() 给非 2xx 错误挂 status（409 = capability 被占用）。
const isConflictError = (err: unknown) =>
  (err as { status?: number } | null)?.status === 409

/**
 * Agent 定义编辑器。发布后的 definition 不可变：编辑已发布 Agent 就是
 * 保存一份新草稿再发布。#407：创建表单不再收集 agent_id——服务端按
 * capability 生成实体键（一个 capability 一个主草稿，占用时返回 409
 * 引导直接编辑）；编辑态 agent_id 只读展示。
 */
export function AgentEditor({
  workspaceId,
  agentId,
  initialCapability,
  onSaved,
  onChanged,
  onArchived,
}: Props) {
  const creating = agentId === null
  const [capability, setCapability] = useState(initialCapability ?? '')
  const [runtime, setRuntime] = useState<AgentRuntime>('velites')
  // #76：skill 不是表单字段（绑定在节点级）；这里只缓存已加载定义的现值，
  // 保存草稿时原样保留（legacy 兜底），新建 Agent 才传空。
  const [skill, setSkill] = useState('')
  const [tools, setTools] = useState<string[]>(['read', 'write', 'bash'])
  const [requiresLabels, setRequiresLabels] = useState<
    Record<string, string> | undefined
  >(undefined)
  const [configSchemaText, setConfigSchemaText] = useState('')
  const [hasDraft, setHasDraft] = useState(false)
  const [loading, setLoading] = useState(!creating)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [versionsOpen, setVersionsOpen] = useState(false)
  const showToast = useUiStore((s) => s.showToast)

  const load = useCallback(() => {
    if (creating) return Promise.resolve()
    return fetchAgentDefinition(workspaceId, agentId)
      .then((detail) => {
        const draft = detail.latest?.status === 'draft' ? detail.latest : null
        const source = draft ?? detail.published ?? detail.latest
        setHasDraft(draft !== null)
        const definition = (source?.definition ?? {}) as Record<string, unknown>
        setCapability(String(definition.capability ?? ''))
        setRuntime((definition.runtime as AgentRuntime) ?? 'velites')
        setSkill(String(definition.skill ?? ''))
        setTools(
          Array.isArray(definition.tools) ? definition.tools.map(String) : []
        )
        setRequiresLabels(
          definition.requires_labels as Record<string, string> | undefined
        )
        setConfigSchemaText(
          definition.config_schema
            ? JSON.stringify(definition.config_schema, null, 2)
            : ''
        )
      })
      .catch((err) => {
        setError(errorMessage(err))
      })
  }, [workspaceId, agentId, creating])

  useEffect(() => {
    // The parent keys this component by agent id, so the load runs once
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

  function buildPayload(): AgentDefinitionPayload | null {
    let configSchema: Record<string, unknown> | undefined
    const text = configSchemaText.trim()
    if (text !== '') {
      try {
        const parsed: unknown = JSON.parse(text)
        if (
          typeof parsed !== 'object' ||
          parsed === null ||
          Array.isArray(parsed)
        )
          throw new Error('config_schema 必须是 JSON 对象')
        configSchema = parsed as Record<string, unknown>
      } catch (err) {
        setError(
          err instanceof Error && err.message.startsWith('config_schema')
            ? err.message
            : 'config_schema 不是合法 JSON'
        )
        return null
      }
    }
    return {
      capability: capability.trim(),
      runtime,
      // #76：skill 不再是 Agent 表单字段（节点级绑定）；generated 契约里
      // skill 仍必填（服务端默认 ""）——编辑存量 Agent 原样保留已加载值
      // （节点未绑 skill 的 workflow 靠它兜底），新建才传空。
      skill: creating ? '' : skill,
      ...(tools.length > 0 ? { tools } : {}),
      ...(requiresLabels ? { requires_labels: requiresLabels } : {}),
      ...(configSchema ? { config_schema: configSchema } : {}),
    }
  }

  async function handleSaveDraft() {
    const payload = buildPayload()
    if (!payload) return
    setError('')
    setBusy(true)
    try {
      if (creating) {
        // #407：payload 不带 agent_id——服务端按 capability 生成；toast 与
        // 后续跳转都用服务端返回的 agent_id。
        const created = await createAgentDefinition(workspaceId, payload)
        showToast(`Agent「${created.agent_id}」草稿已创建`, 'success')
        onSaved(created.agent_id)
      } else {
        await saveAgentDraft(workspaceId, agentId, payload)
        setHasDraft(true)
        showToast('草稿已保存', 'success')
        onChanged()
      }
    } catch (err) {
      setError(errorMessage(err))
      // #436 独立复审：创建 409 引导「请直接编辑」，但占用者可能还没进
      // 列表缓存——对 409 同样触发 onChanged 失效重取，引导入口一键可达。
      if (creating && isConflictError(err)) onChanged()
    } finally {
      setBusy(false)
    }
  }

  async function handlePublish() {
    if (creating) return
    setError('')
    setBusy(true)
    try {
      await publishAgent(workspaceId, agentId)
      setHasDraft(false)
      showToast('已发布', 'success')
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleArchive() {
    if (creating) return
    if (!window.confirm(`确定要归档 Agent「${agentId}」吗？`)) return
    setError('')
    setBusy(true)
    try {
      await archiveAgent(workspaceId, agentId)
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
      {/* #407：创建表单不再有 Agent ID 输入（服务端按 capability 生成）；
          编辑态 agent_id 改不了，只读展示留作身份信息。 */}
      {!creating && (
        <div className={styles.field}>
          <TextField
            label="Agent ID"
            variant="outlined"
            value={agentId}
            fullWidth
            slotProps={{ input: { readOnly: true } }}
          />
        </div>
      )}
      <div className={styles.field}>
        <TextField
          label="Capability"
          variant="outlined"
          value={capability}
          onChange={(e) => setCapability(e.target.value)}
          fullWidth
        />
      </div>
      <div className={styles.field}>
        <TextField
          select
          label="Runtime"
          variant="outlined"
          value={runtime}
          onChange={(e) => setRuntime(e.target.value as AgentRuntime)}
          fullWidth
        >
          {runtimes.map((r) => (
            <MenuItem key={r} value={r}>
              {r}
            </MenuItem>
          ))}
        </TextField>
      </div>
      <div className={styles.field}>
        <TextField
          select
          label="Tools"
          variant="outlined"
          value={tools}
          onChange={(e) => {
            const next = e.target.value
            setTools(typeof next === 'string' ? next.split(',') : next)
          }}
          fullWidth
          slotProps={{ select: { multiple: true } }}
        >
          {toolOptions.map((tool) => (
            <MenuItem key={tool} value={tool}>
              {tool}
            </MenuItem>
          ))}
        </TextField>
      </div>
      <div className={styles.field}>
        <TextField
          label="config_schema（JSON，可空）"
          variant="outlined"
          value={configSchemaText}
          onChange={(e) => setConfigSchemaText(e.target.value)}
          fullWidth
          multiline
          minRows={3}
          placeholder='{"type":"object","properties":{...}}'
        />
      </div>
      <div className={styles.editorActions}>
        <Button
          variant="contained"
          onClick={() => void handleSaveDraft()}
          disabled={busy || capability.trim() === ''}
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
        <AgentVersionsDialog
          workspaceId={workspaceId}
          agentId={agentId}
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
