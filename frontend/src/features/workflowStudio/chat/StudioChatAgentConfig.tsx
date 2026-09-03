import { useMemo } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'
import { agentConfigView } from './agentConfigView'
import { NativeSelect, ThoughtLevelField } from './StudioChatAgentConfigFields'
import { useStudioChatAgentConfig } from './useStudioChatAgentConfig'
import { useThoughtDrift } from './useThoughtDrift'
import styles from './StudioChatAgentConfig.module.css'

type Props = {
  workspaceId: string | undefined
  session: StudioChatSessionRecord | null
}

/** 会话面板「agent 配置」区（#368）：权限模式 / 模型 / 思考档位 + 高级设置。
 * 不广告配置面的 agent 整体隐藏（现有体验不变）。ACP 权限模式是 agent 自律，
 * 平台侧「本次对话全部允许」是平台强制——两层独立设置、分开可见、不自动互改。 */
export function StudioChatAgentConfig(props: Props) {
  const config = useStudioChatAgentConfig(props.workspaceId, props.session)
  const view = useMemo(() => agentConfigView(config.session), [config.session])
  const drift = useThoughtDrift(
    view.thought?.map.current ?? null,
    config.lastAction !== null && config.lastAction === view.thought?.id
  )
  if (!view.visible) return null
  const busy = config.pending !== null
  return (
    <div className={styles.bar} role="group" aria-label="Agent 配置">
      {view.modes && (
        <label className={styles.field}>
          权限模式
          <select
            className={styles.select}
            aria-label="Agent 权限模式"
            value={view.modes.currentModeId}
            disabled={busy}
            onChange={(event) => void config.setMode(event.target.value)}
          >
            {view.modes.available.map((mode) => (
              <option key={mode.id} value={mode.id} title={mode.description}>
                {mode.name}
              </option>
            ))}
          </select>
          <span
            className={styles.hint}
            title="这是 agent 自身的运行模式；平台侧「本次对话全部允许」在权限卡上独立设置，两层互不改写。"
          >
            ⓘ
          </span>
        </label>
      )}
      {view.model && (
        <NativeSelect
          entry={view.model}
          label="模型"
          disabled={busy}
          onChange={(value) => void config.setOption(view.model!.id, value)}
        />
      )}
      {view.thought && (
        <ThoughtLevelField
          thought={view.thought}
          disabled={busy}
          onChange={(value) => void config.setOption(view.thought!.id, value)}
        />
      )}
      {view.advanced.length > 0 && (
        <details className={styles.advanced}>
          <summary>高级设置</summary>
          <div className={styles.advancedBody}>
            {view.advanced.map((entry) =>
              entry.type === 'select' ? (
                <NativeSelect
                  key={entry.id}
                  entry={entry}
                  label={entry.name}
                  disabled={busy}
                  onChange={(value) => void config.setOption(entry.id, value)}
                />
              ) : (
                <span
                  key={entry.id}
                  className={styles.readOnly}
                  title={entry.description}
                >
                  {entry.name}：{entry.currentValue}（只读）
                </span>
              )
            )}
          </div>
        </details>
      )}
      {drift && (
        <span className={styles.drift} role="status">
          {drift}
        </span>
      )}
      {config.error && (
        <span className={styles.error} role="alert">
          {config.error}
        </span>
      )}
    </div>
  )
}
