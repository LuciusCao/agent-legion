import { useMemo } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'
import { agentConfigView, flattenOptions } from './agentConfigView'
import {
  advancedOptions,
  modeOptions,
  modelOptions,
  THOUGHT_OFF_VALUE,
  thoughtOptions,
  thoughtText,
} from './studioChatConfigOptions'
import { UI_LEVELS, levelLabel, type UiLevel } from './thoughtLevel'
import { useStudioChatAgentConfig } from './useStudioChatAgentConfig'
import { useThoughtDrift } from './useThoughtDrift'
import { StudioChatConfigChip } from './StudioChatConfigChip'
import styles from './StudioChatComposer.module.css'

/** composer 工具行里的配置芯片组（#695 R4）：呈现从 StudioChatAgentConfig 的
 * 原生 select 大盒子换成「文本 + ▾」chip + MUI Menu；取值、映射与提交完全
 * 复用同一数据层（useStudioChatAgentConfig / agentConfigView / thoughtLevel，
 * 权限模式语义 #658：agent 自律模式与平台「全部允许」两层互不改写）。
 * 左组（权限模式 + 漂移/错误提示）与右组（模型/思考档位/高级）经 fragment
 * 直接成为 toolbar 的 flex 子项，右推由 configRight 的 margin-left:auto 承担。 */
export function StudioChatComposerConfig(props: {
  workspaceId: string | undefined
  session: StudioChatSessionRecord | null
}) {
  const config = useStudioChatAgentConfig(props.workspaceId, props.session)
  const view = useMemo(() => agentConfigView(config.session), [config.session])
  const drifted = useThoughtDrift(
    config.session?.id ?? null,
    view.thought?.map.current ?? null,
    config.lastAction === view.thought?.id ? config.lastActionToken : null
  )
  if (!view.visible) return <span className={styles.toolbarSpacer} />
  // 与输入框的禁用条件对齐：终态会话（closed/error）上切配置只会得到 409。
  const status = config.session?.status
  const busy =
    config.pending !== null || status === 'closed' || status === 'error'
  const modelText = view.model
    ? (flattenOptions(view.model.options).find(
        (option) => option.value === view.model!.currentValue
      )?.name ?? view.model.currentValue)
    : ''
  return (
    <>
      <span className={styles.configLeft} role="group" aria-label="Agent 配置">
        {view.modes && (
          <StudioChatConfigChip
            label="Agent 权限模式"
            text={
              view.modes.available.find(
                (mode) => mode.id === view.modes!.currentModeId
              )?.name ?? view.modes.currentModeId
            }
            title="这是 agent 自身的运行模式；平台侧「本次对话全部允许」在权限卡上独立设置，两层互不改写。"
            disabled={busy}
            options={modeOptions(view.modes)}
            onPick={(value) => void config.setMode(value)}
          />
        )}
        {drifted && view.thought?.map.current && (
          <span className={styles.drift} role="status">
            思考档位已随模型切换变为{' '}
            {levelLabel(view.thought.map.current, view.thought.currentValue)}
          </span>
        )}
        {config.error && (
          <span className={styles.error} role="alert">
            {config.error}
          </span>
        )}
      </span>
      <span className={styles.configRight}>
        {view.model && (
          <StudioChatConfigChip
            label="模型"
            text={modelText}
            title={view.model.description}
            disabled={busy}
            options={modelOptions(view.model)}
            onPick={(value) => void config.setOption(view.model!.id, value)}
          />
        )}
        {view.thought && (
          <StudioChatConfigChip
            label="思考档位"
            text={thoughtText(view.thought)}
            title={
              view.thought.map.readOnly
                ? '该模型不可调'
                : view.thought.description
            }
            disabled={busy || view.thought.map.readOnly}
            options={thoughtOptions(view.thought)}
            onPick={(value) => {
              const { map } = view.thought!
              // 通用档走映射；未知原生值与 off 原样透传（同原 ThoughtLevelField）。
              const native =
                value === THOUGHT_OFF_VALUE
                  ? map.offValue!
                  : (UI_LEVELS as readonly string[]).includes(value)
                    ? (map.toNative[value as UiLevel] ?? value)
                    : value
              void config.setOption(view.thought!.id, native)
            }}
          />
        )}
        {view.advanced.length > 0 && (
          <StudioChatConfigChip
            label="高级设置"
            text="高级"
            disabled={busy}
            options={advancedOptions(view.advanced)}
            onPick={(value) => {
              const separator = value.indexOf(':')
              void config.setOption(
                value.slice(0, separator),
                value.slice(separator + 1)
              )
            }}
          />
        )}
      </span>
    </>
  )
}
