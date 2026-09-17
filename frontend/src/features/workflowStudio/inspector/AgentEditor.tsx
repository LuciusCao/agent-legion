import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Chip, MenuItem, TextField } from '@mui/material'
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
import { useRuntimeToolEntries } from './useAgentRuntimes'
import styles from './AgentsPanel.module.css'

// #408：velites（自研 harness，流式事件 + token 计量）是默认且优先级
// 更高的 runtime，排在选项首位；pi 是外部 runtime，仅作备选。
const runtimes: AgentRuntime[] = ['velites', 'pi']

// #476：forced 档的锁定行说明（validate 不渲染成可勾选开关——退出契约
// 门由节点 outputs 声明激活，勾不勾 validate 关不掉校验，渲染成开关是
// 语义陷阱）。
const forcedTierHint =
  'harness 强制：节点声明 outputs 时经 --require-output 激活，退出契约门不可取消；工具开关仅控制模型 mid-run 自检'

// #575：Tools 字段的兜底默认提示——本组件只内嵌于节点详情使用（唯一
// 生产调用点是 WorkflowNodeAgentEditorPanel），节点级「Tools 覆盖」是
// 主入口，本字段只是 Agent 定义的兜底默认（#440 终局并入节点 YAML）。
const toolsFallbackHint = '兜底默认——节点级「Tools 覆盖」优先，建议按节点覆盖'

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

// api() 给非 2xx 错误挂 status（409 = capability 被占用 / #749 CAS 草稿被覆盖）。
const isConflictError = (err: unknown) =>
  (err as { status?: number } | null)?.status === 409

// #749：发布 CAS（expected_hash）被服务端拒绝的专用文案——草稿在保存后被
// 其他会话/编辑器覆盖，本地表单已不是要发布的身份。与聊天草稿卡的 409
// 文案同一交互模式：内联提示 + 引导从最新草稿重来，不发明新 UI。
const DRAFT_OVERRIDDEN_HINT = '草稿已被其他会话或编辑器更新，请刷新后重新保存再发布'

/**
 * Agent 定义编辑器。发布后的 definition 不可变：编辑已发布 Agent 就是
 * 保存一份新草稿再发布。#407：创建表单不再收集 agent_id——服务端按
 * capability 生成实体键（一个 capability 一个主草稿，占用时返回 409
 * 引导直接编辑）；编辑态 agent_id 只读展示。
 *
 * #476：工具选项面按所选 runtime 从 GET /api/agent-runtimes 动态拉取
 * （目录与 dispatch 校验同源）——default 预选中、opt-in 显式开启、
 * forced 渲染锁定行；runtime 切换后失效工具显式标记（uuid 在 pi 下
 * 不存在），把 dispatch fail-fast 前移到编辑体验。
 *
 * #575：Tools 字段只有一种形态——「Agent 默认 / 兜底」标注（本组件
 * 唯一生产调用点是节点详情的内嵌面板，节点级「Tools 覆盖」是主入口；
 * #440 终局定义层字段并入节点 YAML 时整体删除）。
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
  // #476：null = 用户未改动且定义未回填——生效值由目录 default 档派生
  //（预选不再前端硬编码）；一旦用户改动或定义回填即固化。
  const [toolsOverride, setToolsOverride] = useState<string[] | null>(null)
  const [requiresLabels, setRequiresLabels] = useState<
    Record<string, string> | undefined
  >(undefined)
  const [configSchemaText, setConfigSchemaText] = useState('')
  const [hasDraft, setHasDraft] = useState(false)
  // #749：当前草稿的 definition_hash——发布的 CAS 令牌。三个来源同步它：
  // 创建/保存草稿的响应（本面板写入的身份）、详情加载里的草稿行（发布
  // 别处保存的草稿，如 MCP 工具面建的）、发布成功后清空。空值 = 无可核
  // 验身份，发布按钮禁用（无 hash 的发布在草稿被覆盖时会静默发别人的
  // 内容，与聊天卡 codex P1 第四轮同一立场）。
  const [draftHash, setDraftHash] = useState('')
  const [loading, setLoading] = useState(!creating)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [versionsOpen, setVersionsOpen] = useState(false)
  const showToast = useUiStore((s) => s.showToast)

  const toolEntries = useRuntimeToolEntries(runtime)
  const defaultToolNames = useMemo(
    () =>
      (toolEntries ?? [])
        .filter((entry) => entry.tier === 'default')
        .map((entry) => entry.name),
    [toolEntries]
  )
  // 生效值：用户/定义值优先，否则目录 default 预选（目录未加载时暂空）。
  const tools = toolsOverride ?? defaultToolNames

  const load = useCallback(() => {
    if (creating) return Promise.resolve()
    return fetchAgentDefinition(workspaceId, agentId)
      .then((detail) => {
        const draft = detail.latest?.status === 'draft' ? detail.latest : null
        const source = draft ?? detail.published ?? detail.latest
        setHasDraft(draft !== null)
        // #749：草稿身份随详情同步（如 MCP 工具面保存的草稿，本面板只
        // 发布不保存）；无草稿时清空，禁用发布按钮。
        setDraftHash(draft?.definition_hash ?? '')
        const definition = (source?.definition ?? {}) as Record<string, unknown>
        setCapability(String(definition.capability ?? ''))
        setRuntime((definition.runtime as AgentRuntime) ?? 'velites')
        setSkill(String(definition.skill ?? ''))
        setToolsOverride(
          Array.isArray(definition.tools) ? definition.tools.map(String) : null
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
      .catch((err) => setError(errorMessage(err)))
  }, [workspaceId, agentId, creating])

  useEffect(() => {
    // The parent keys this component by agent id, so the load runs once
    // per mount and `loading` starts true via its useState initializer.
    if (creating) return
    let cancelled = false
    void load().finally(() => {
      if (!cancelled) {
        setLoading(false)
      }
    })
    return () => {
      cancelled = true
    }
  }, [load, creating])

  // 选项面：default / opt-in 可选；forced 不进可勾选集。
  const selectableEntries = useMemo(
    () => (toolEntries ?? []).filter((entry) => entry.tier !== 'forced'),
    [toolEntries]
  )
  const selectableNames = useMemo(
    () => new Set(selectableEntries.map((entry) => entry.name)),
    [selectableEntries]
  )
  const forcedEntries = useMemo(
    () => (toolEntries ?? []).filter((entry) => entry.tier === 'forced'),
    [toolEntries]
  )
  // runtime 切换后的失效项：已选但当前 runtime 目录不提供（dispatch 会
  // fail-fast；编辑期显式标记引导剔除）。
  const invalidTools = useMemo(
    () => tools.filter((tool) => !selectableNames.has(tool)),
    [tools, selectableNames]
  )

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
        // #749：创建响应即草稿身份，发布时作为 expected_hash 带回。
        setDraftHash(created.definition_hash)
        onSaved(created.agent_id)
      } else {
        const saved = await saveAgentDraft(workspaceId, agentId, payload)
        setHasDraft(true)
        // #749：保存响应的 hash 是新草稿身份（后续发布的 CAS 令牌）。
        setDraftHash(saved.definition_hash)
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
      // #749：带上保存/加载时的草稿 hash，服务端在发布事务内 CAS 核对
      // ——不匹配 409 零副作用。draftHash 为空（异常形态）不发布。
      await publishAgent(workspaceId, agentId, draftHash)
      setHasDraft(false)
      setDraftHash('')
      showToast('已发布', 'success')
      onChanged()
    } catch (err) {
      // 409 双语义：CAS 拒绝（草稿被覆盖）用引导刷新的专用文案（与聊天
      // 草稿卡同一模式）；capability 占用仍是后端 detail 直显。
      setError(isConflictError(err) ? DRAFT_OVERRIDDEN_HINT : errorMessage(err))
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
          label="Tools（Agent 默认 / 兜底）"
          variant="outlined"
          value={tools}
          onChange={(e) => {
            const next = e.target.value
            setToolsOverride(typeof next === 'string' ? next.split(',') : next)
          }}
          fullWidth
          disabled={!toolEntries}
          helperText={toolsFallbackHint}
          slotProps={{ select: { multiple: true } }}
        >
          {selectableEntries.map((entry) => (
            <MenuItem key={entry.name} value={entry.name}>
              {entry.name}
              {entry.tier === 'opt-in' ? '（可选开启）' : ''}
            </MenuItem>
          ))}
        </TextField>
        {/* #476：forced 档锁定行——不是可勾选开关，语义见 forcedTierHint。 */}
        {forcedEntries.map((entry) => (
          <p key={entry.name} className={styles.hint}>
            {entry.name}（harness 强制，{entry.activation}）——{forcedTierHint}
          </p>
        ))}
        {/* codex P2 on #527：失效项渲染为可点的移除 chip——多选下拉无法
            取消禁用项，留剔除去处（原先只提示「请剔除」却无入口）。 */}
        {invalidTools.length > 0 && (
          <div className={styles.error} role="alert">
            已选工具不在 runtime {runtime} 的目录里——dispatch 会拒绝，请移除：
            {invalidTools.map((tool) => (
              <Chip
                key={tool}
                size="small"
                color="error"
                label={tool}
                onDelete={() =>
                  setToolsOverride(
                    tools.filter((selected) => selected !== tool)
                  )
                }
                sx={{ marginLeft: 1 }}
              />
            ))}
          </div>
        )}
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
            disabled={busy || !hasDraft || !draftHash}
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
            // 同步表单，并清掉 draft 标记与草稿身份（#749）。
            void load().then(() => {
              setHasDraft(false)
              setDraftHash('')
            })
            onChanged()
          }}
        />
      )}
    </div>
  )
}
