import { UI_LEVELS, levelLabel, type UiLevel } from './thoughtLevel'
import { isGroup, type ConfigEntry, type ThoughtView } from './agentConfigView'
import styles from './StudioChatAgentConfig.module.css'

/** 会话配置面板的两类控件（拆自 StudioChatAgentConfig.tsx，文件预算）。 */

type SelectProps = {
  entry: ConfigEntry
  label: string
  disabled: boolean
  onChange: (value: string) => void
}

/** 原生渲染 agent 广告的 select（模型与高级设置）：值跨家无语义，不做归一；
 * 分组形态（协议 union 的另一半）渲染为 optgroup。 */
export function NativeSelect({
  entry,
  label,
  disabled,
  onChange,
}: SelectProps) {
  const renderOption = (option: {
    value: string
    name?: string
    description?: string
  }) => (
    <option key={option.value} value={option.value} title={option.description}>
      {option.name ?? option.value}
    </option>
  )
  return (
    <label className={styles.field} title={entry.description}>
      {label}
      <select
        className={styles.select}
        aria-label={label}
        value={entry.currentValue}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {entry.options.map((option) =>
          isGroup(option) ? (
            <optgroup key={option.group} label={option.name}>
              {option.options.map(renderOption)}
            </optgroup>
          ) : (
            renderOption(option)
          )
        )}
      </select>
    </label>
  )
}

type ThoughtProps = {
  thought: ThoughtView
  disabled: boolean
  onChange: (nativeValue: string) => void
}

/** 通用思考档位：用户只见一套档位词（low/medium/high/max），落到 agent 真实
 * 档位值；落点与档位词不同的标注「→ 实际值」（kimi 无 medium 时 medium→low）。
 * off 渲染为独立开关；单档列表降级只读；agent 广告的未知值（如 turbo）作为
 * 原生回退选项附在通用档之后——看得见、选得到、切走后切得回。 */
export function ThoughtLevelField({
  thought,
  disabled,
  onChange,
}: ThoughtProps) {
  const { map } = thought
  const uiValue: string =
    map.current === 'off' ? '' : (map.current ?? thought.currentValue)
  const firstOn =
    map.toNative.medium ??
    map.toNative.low ??
    map.toNative.high ??
    map.toNative.max ??
    map.unknownValues[0]
  // 通用档走映射；未知原生值原样透传（value 不在 UI_LEVELS 里即为原生值）。
  const resolve = (picked: string) =>
    (UI_LEVELS as readonly string[]).includes(picked)
      ? (map.toNative[picked as UiLevel] ?? picked)
      : picked
  return (
    <label className={styles.field} title={thought.description}>
      思考档位
      {map.offValue !== null && (
        <input
          type="checkbox"
          aria-label="思考开关"
          checked={map.current !== 'off'}
          disabled={disabled || map.readOnly || firstOn === undefined}
          onChange={(event) =>
            onChange(event.target.checked ? firstOn! : map.offValue!)
          }
        />
      )}
      <select
        className={styles.select}
        aria-label="思考档位"
        value={uiValue}
        disabled={disabled || map.readOnly || map.current === 'off'}
        onChange={(event) => onChange(resolve(event.target.value))}
      >
        {map.current === 'off' && <option value="">关闭</option>}
        {UI_LEVELS.filter((ui) => map.toNative[ui] !== undefined).map((ui) => (
          <option key={ui} value={ui}>
            {levelLabel(ui, map.toNative[ui])}
          </option>
        ))}
        {map.unknownValues.map((value) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
      {map.readOnly && (
        <span className={styles.readOnly}>（该模型不可调）</span>
      )}
    </label>
  )
}
