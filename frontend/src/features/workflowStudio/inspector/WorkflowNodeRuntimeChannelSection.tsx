import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Chip } from '@mui/material'
import type { WorkflowNodeRecord } from '../../../types'
import { useStudioStateOptional } from '../shared/studioStateContext'
import {
  parseWorkflowNode,
  patchWorkflowNodeRequiresLabels,
  patchWorkflowNodeTools,
} from '../shared/workflowStudioYamlDraft'
import {
  asConfigValue,
  patchWorkflowNodeExecution,
} from '../shared/workflowStudioYamlDraft.execution'
import { normalizeStringList } from '../shared/workflowStudioYamlDraft.parse'
import { parseWorkflowExecutionDefaults } from '../shared/workflowStudioYamlDraft.executionDefaults'
import { useWorkspaceRuntimeModels } from '../shared/useWorkspaceRuntimeModels'
import { useRuntimeModelOptions } from './runtimeModelOptions'
import { useRuntimeToolEntries } from './useAgentRuntimes'
import { useNodeRuntimeInfo } from './WorkflowNodeProfileBody'
import { WorkflowChannelBadge } from './WorkflowChannelBadge'
import { WorkflowChipEditor } from './WorkflowChipEditor'
import { WorkflowNodeThinkingField } from './WorkflowNodeThinkingField'
import { WorkflowRuntimeInheritedField } from './WorkflowRuntimeInheritedField'
import { WorkflowRuntimeVersionsDialog } from './WorkflowRuntimeVersionsDialog'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowNodeRuntimeChannelSection.module.css'

type RuntimeField = 'provider' | 'model' | 'thinking' | 'prompt' | 'prompt_mode'

function labelsEqual(a: Record<string, string>, b: Record<string, string>) {
  const keysA = Object.keys(a)
  const keysB = Object.keys(b)
  return (
    keysA.length === keysB.length && keysA.every((key) => a[key] === b[key])
  )
}

function listEqual(a: string[], b: string[]) {
  return a.length === b.length && a.every((item, index) => item === b[index])
}

/** 「已修改 · 待保存」/「已生效」字段状态点。 */
function FieldDot(props: { dirty: boolean; saved: boolean }) {
  if (props.dirty) {
    return <span className={styles.dirtyDot}>已修改 · 待保存</span>
  }
  if (props.saved) {
    return <span className={styles.savedDot}>已生效</span>
  }
  return null
}

/**
 * M48 运行配置专区（用户确认原型）：agent 节点的 runtime 字段
 * （provider/model/thinking/prompt/prompt_mode + tools/requires_labels）
 * 独立成区——绿色描边卡片 +「运行通道 · 即改即存」徽标 + 独立保存按钮
 * （运行通道：立即生效，不产生新版本）。编辑仍写草稿 YAML（单一事实源），
 * 但 execution-only 变更不点亮「未发布变更」（见 workflowStudioChanges）。
 * 只读 revision：横幅 + 全部控件禁用。自含执行档案缺失的 legacy 节点不渲染
 * （沿用 LegacyAgentBody 的既有呈现）。
 */
export function WorkflowNodeRuntimeChannelSection(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const { node } = props
  const studio = useStudioStateOptional()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [versionsOpen, setVersionsOpen] = useState(false)
  // Prompt 分段控件的本地 UI 态：点「自定义」立即点亮输入区（与原型一致），
  // 不写 YAML——等用户输入才有草稿改动。state 带 nodeKey 标记，切节点后旧
  // 覆盖自动失效（不落 effect 重置）。
  const [promptUi, setPromptUi] = useState<{
    nodeKey: string
    mode: 'default' | 'custom'
  } | null>(null)
  const promptUiOverride = promptUi?.nodeKey === node.key ? promptUi.mode : null
  const runtimeInfo = useNodeRuntimeInfo(node, props.definitionYaml)
  const effectiveRuntime = runtimeInfo.effectiveRuntime

  const draftNode = useMemo(
    () => parseWorkflowNode(props.definitionYaml, node.key),
    [props.definitionYaml, node.key]
  )
  const originalYaml = studio?.originalYaml
  const baselineNode = useMemo(
    () =>
      originalYaml ? parseWorkflowNode(originalYaml, node.key) : undefined,
    [originalYaml, node.key]
  )
  const defaults = useMemo(
    () => parseWorkflowExecutionDefaults(props.definitionYaml),
    [props.definitionYaml]
  )

  const execution = draftNode?.execution ?? node.execution ?? {}
  const baselineExecution = baselineNode?.execution ?? {}

  const currentValue = (field: RuntimeField) => asConfigValue(execution[field])
  const baselineValue = (field: RuntimeField) =>
    asConfigValue(baselineExecution[field])
  const savedKeys = studio?.lastRuntimeSavedKeys ?? []
  const fieldDirty = (field: RuntimeField) =>
    currentValue(field) !== baselineValue(field)
  const fieldSaved = (field: RuntimeField) =>
    !fieldDirty(field) && savedKeys.includes(`nodes.${node.key}.${field}`)

  const tools = normalizeStringList(draftNode?.tools ?? node.tools)
  const baselineTools = normalizeStringList(baselineNode?.tools)
  const toolsDirty = !listEqual(tools, baselineTools)
  const toolsSaved =
    !toolsDirty && savedKeys.includes(`nodes.${node.key}.tools`)

  const requiresLabels = useMemo(() => {
    const raw = draftNode?.requires_labels ?? {}
    const labels: Record<string, string> = {}
    for (const [key, value] of Object.entries(raw)) {
      labels[key] = typeof value === 'string' ? value : ''
    }
    return labels
  }, [draftNode])
  const baselineLabels = useMemo(() => {
    const raw = baselineNode?.requires_labels ?? {}
    const labels: Record<string, string> = {}
    for (const [key, value] of Object.entries(raw)) {
      labels[key] = typeof value === 'string' ? value : ''
    }
    return labels
  }, [baselineNode])
  const labelsDirty = !labelsEqual(requiresLabels, baselineLabels)
  const labelsSaved =
    !labelsDirty &&
    savedKeys.some((key) => key.startsWith(`nodes.${node.key}.requires_labels`))

  const toolEntries = useRuntimeToolEntries(
    effectiveRuntime as 'pi' | 'velites'
  )
  const selectableTools = useMemo(
    () => (toolEntries ?? []).filter((entry) => entry.tier !== 'forced'),
    [toolEntries]
  )
  const invalidTools = useMemo(
    () =>
      toolEntries
        ? tools.filter(
            (tool) => !selectableTools.some((entry) => entry.name === tool)
          )
        : [],
    [tools, selectableTools, toolEntries]
  )
  const toolAddOptions = selectableTools
    .map((entry) => entry.name)
    .filter((name) => !tools.includes(name))
  const fallbackTools = toolEntries
    ?.filter((entry) => entry.tier === 'default')
    .map((entry) => entry.name)

  const { providerOptions, modelOptions } = useRuntimeModelOptions(
    useWorkspaceRuntimeModels(workspaceId).data?.runtimes,
    effectiveRuntime,
    currentValue('provider')
  )

  if (node.node_type !== 'agent' || !effectiveRuntime) return null

  const patch = (field: RuntimeField, value: string) =>
    props.setDefinitionYaml(
      patchWorkflowNodeExecution(props.definitionYaml, node.key, field, value)
    )

  const promptYamlMode: 'default' | 'custom' =
    currentValue('prompt') || currentValue('prompt_mode') ? 'custom' : 'default'
  const promptMode = promptUiOverride ?? promptYamlMode
  const promptJoinMode =
    currentValue('prompt_mode') === 'overwrite' ? 'overwrite' : 'append'

  const setPromptMode = (mode: 'default' | 'custom') => {
    if (mode === 'custom') {
      setPromptUi({ nodeKey: node.key, mode: 'custom' })
      return
    }
    // 回到默认：清掉自定义 prompt 与拼接模式（随 skill 默认组装）。
    setPromptUi(null)
    if (promptYamlMode === 'default') return
    props.setDefinitionYaml(
      patchWorkflowNodeExecution(
        patchWorkflowNodeExecution(
          props.definitionYaml,
          node.key,
          'prompt',
          ''
        ),
        node.key,
        'prompt_mode',
        ''
      )
    )
  }

  const runtimeChangedKeys = studio?.runtimeChangedKeys ?? []
  const saveDisabled =
    !studio ||
    props.readOnly === true ||
    studio.runtimeSaving ||
    studio.runtimeSaveUnavailable ||
    runtimeChangedKeys.length === 0
  const saveHint = props.readOnly
    ? '只读视图：运行配置区整体禁用。'
    : runtimeChangedKeys.length > 0
      ? '运行配置有未保存修改：点下方按钮即刻生效，不受结构改动 / 发布流程影响。'
      : savedKeys.length > 0
        ? '本次运行配置已生效。继续修改后可再次保存，同样不产生版本。'
        : '运行配置独立于草稿 / 发布链路：点保存即刻写入并对新执行生效，不会触发「未发布变更」。'

  const labelChips = Object.entries(requiresLabels).map(([key, value]) =>
    value ? `${key}=${value}` : key
  )
  const removeLabelChip = (chip: string) => {
    const key = chip.includes('=') ? chip.slice(0, chip.indexOf('=')) : chip
    const next = { ...requiresLabels }
    delete next[key]
    props.setDefinitionYaml(
      patchWorkflowNodeRequiresLabels(props.definitionYaml, node.key, next)
    )
  }
  const addLabelChip = (text: string) => {
    const separator = text.indexOf('=')
    const key = (separator >= 0 ? text.slice(0, separator) : text).trim()
    if (!key || key in requiresLabels) return
    const value = separator >= 0 ? text.slice(separator + 1).trim() : ''
    props.setDefinitionYaml(
      patchWorkflowNodeRequiresLabels(props.definitionYaml, node.key, {
        ...requiresLabels,
        [key]: value,
      })
    )
  }

  const datalistPrefix = `runtime-channel-${node.key}`

  return (
    <section className={inspectorStyles.section} aria-label="运行配置">
      <div className={styles.card}>
        {props.readOnly && (
          <div className={styles.readonlyBanner} role="note">
            <span className={styles.readonlyBannerIcon}>🔒</span>
            <span>
              <b>查看历史版本时不可修改运行配置。</b>
              <br />
              当前为只读视图；如需调整，请回到草稿或「设为草稿」后编辑。
            </span>
          </div>
        )}
        <div className={styles.titleRow}>
          运行配置
          <WorkflowChannelBadge kind="runtime" />
          {savedKeys.length > 0 && !props.readOnly && (
            <span className={styles.savedDot}>已生效</span>
          )}
          <span className={styles.titleSpacer} />
          <button
            type="button"
            className={styles.historyButton}
            onClick={() => setVersionsOpen(true)}
          >
            版本历史
          </button>
        </div>
        <p className={styles.lead}>
          保存后立即对新执行生效，<b>不产生新版本</b>
          ；进行中的任务继续使用启动时的配置，不受影响。
        </p>

        <div className={styles.fields}>
          <WorkflowRuntimeInheritedField
            label="Provider"
            value={currentValue('provider')}
            inherited={defaults.provider ?? ''}
            options={providerOptions}
            listId={`${datalistPrefix}-provider`}
            readOnly={props.readOnly}
            labelSuffix={
              <FieldDot
                dirty={fieldDirty('provider')}
                saved={fieldSaved('provider')}
              />
            }
            onChange={(value) => patch('provider', value)}
          />
          <WorkflowRuntimeInheritedField
            label="Model"
            value={currentValue('model')}
            inherited={defaults.model ?? ''}
            options={modelOptions}
            listId={`${datalistPrefix}-model`}
            readOnly={props.readOnly}
            labelSuffix={
              <FieldDot
                dirty={fieldDirty('model')}
                saved={fieldSaved('model')}
              />
            }
            onChange={(value) => patch('model', value)}
          />
          <WorkflowNodeThinkingField
            value={currentValue('thinking')}
            inherited={defaults.thinking ?? ''}
            readOnly={props.readOnly}
            labelSuffix={
              <FieldDot
                dirty={fieldDirty('thinking')}
                saved={fieldSaved('thinking')}
              />
            }
            onChange={(value) => patch('thinking', value)}
          />

          <div>
            <div className={styles.fieldLabelRow}>Prompt 模式</div>
            <div
              className={`${styles.segmented} ${props.readOnly ? styles.segmentedDisabled : ''}`}
              role="group"
              aria-label="Prompt 模式"
            >
              <button
                type="button"
                className={promptMode === 'default' ? styles.active : ''}
                disabled={props.readOnly}
                onClick={() => setPromptMode('default')}
              >
                默认（随 skill）
              </button>
              <button
                type="button"
                className={promptMode === 'custom' ? 'active' : ''}
                disabled={props.readOnly}
                onClick={() => setPromptMode('custom')}
              >
                自定义
              </button>
            </div>
          </div>
          <div>
            <div className={styles.fieldLabelRow}>
              Prompt
              <FieldDot
                dirty={fieldDirty('prompt') || fieldDirty('prompt_mode')}
                saved={
                  !fieldDirty('prompt') &&
                  !fieldDirty('prompt_mode') &&
                  (fieldSaved('prompt') || fieldSaved('prompt_mode'))
                }
              />
            </div>
            <textarea
              aria-label="Prompt"
              className={styles.promptArea}
              value={currentValue('prompt')}
              disabled={props.readOnly || promptMode === 'default'}
              placeholder="默认模式下随 skill 的 prompt 执行；切换「自定义」后在此覆盖。"
              onChange={(event) => patch('prompt', event.target.value)}
            />
            {promptMode === 'custom' && (
              <select
                aria-label="Prompt 拼接方式"
                className={styles.modeSelect}
                value={promptJoinMode}
                disabled={props.readOnly}
                onChange={(event) =>
                  patch(
                    'prompt_mode',
                    event.target.value === 'overwrite' ? 'overwrite' : 'append'
                  )
                }
              >
                <option value="append">追加（默认 prompt + 自定义）</option>
                <option value="overwrite">覆写（仅自定义）</option>
              </select>
            )}
            <div className={styles.fieldHint}>
              「查看 Prompt」面板可预览自动组装的完整 prompt。
            </div>
          </div>

          <div>
            <div className={styles.fieldLabelRow}>
              Tools 覆盖
              <FieldDot dirty={toolsDirty} saved={toolsSaved} />
            </div>
            <WorkflowChipEditor
              ariaLabel="Tools 覆盖"
              chips={tools}
              onRemove={(tool) =>
                props.setDefinitionYaml(
                  patchWorkflowNodeTools(
                    props.definitionYaml,
                    node.key,
                    tools.filter((item) => item !== tool)
                  )
                )
              }
              onAdd={(tool) =>
                props.setDefinitionYaml(
                  patchWorkflowNodeTools(props.definitionYaml, node.key, [
                    ...tools,
                    tool,
                  ])
                )
              }
              addLabel="添加工具"
              addOptions={toolAddOptions}
              readOnly={props.readOnly || !toolEntries}
            />
            <div className={styles.fieldHint}>
              留空 = 跟随 runtime 默认档
              {tools.length === 0 && fallbackTools !== undefined
                ? `（当前生效：${fallbackTools.join(', ') || '（空）'}）`
                : ''}
              。可选项来自该 runtime 的工具目录。
            </div>
            {invalidTools.length > 0 && (
              <div className={styles.invalidTools} role="alert">
                已声明工具不在 runtime {effectiveRuntime} 的目录里——执行会拒绝，
                请移除：
                {invalidTools.map((tool) => (
                  <Chip
                    key={tool}
                    size="small"
                    color="error"
                    label={tool}
                    onDelete={() =>
                      props.setDefinitionYaml(
                        patchWorkflowNodeTools(
                          props.definitionYaml,
                          node.key,
                          tools.filter((item) => item !== tool)
                        )
                      )
                    }
                  />
                ))}
              </div>
            )}
          </div>

          <div>
            <div className={styles.fieldLabelRow}>
              Requires labels
              <FieldDot dirty={labelsDirty} saved={labelsSaved} />
            </div>
            <WorkflowChipEditor
              ariaLabel="Requires labels"
              chips={labelChips}
              onRemove={removeLabelChip}
              onAdd={addLabelChip}
              addLabel="添加标签"
              addInputPlaceholder="key 或 key=value"
              readOnly={props.readOnly}
            />
            <div className={styles.fieldHint}>
              Worker 调度标签：仅分配给携带全部标签的 worker；留空 = 任意可用
              worker。格式 key=value，只写 key 表示要求该标签存在。
            </div>
          </div>
        </div>

        {studio && (
          <div className={styles.saveBar}>
            <div className={styles.saveHint}>
              <span className={styles.saveHintIcon}>⚡</span>
              <span>{saveHint}</span>
            </div>
            <button
              type="button"
              className={styles.saveButton}
              disabled={saveDisabled}
              onClick={() => void studio.saveRuntimeConfig()}
            >
              {studio.runtimeSaving ? '保存中…' : '保存运行配置'}
              <small>立即生效，不产生版本</small>
            </button>
          </div>
        )}
        <div className={styles.topNote}>
          提示：workflow 顶层 <code>execution</code> 默认块（provider / model /
          thinking）同属运行通道，同样即改即存。
        </div>
      </div>
      {workspaceId && (
        <WorkflowRuntimeVersionsDialog
          open={versionsOpen}
          workspaceId={workspaceId}
          readOnly={props.readOnly === true}
          onClose={() => setVersionsOpen(false)}
        />
      )}
    </section>
  )
}
