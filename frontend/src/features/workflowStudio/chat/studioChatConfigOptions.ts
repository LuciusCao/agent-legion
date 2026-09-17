import type { ConfigEntry, ModeView, ThoughtView } from './agentConfigView'
import { flattenOptions, isGroup } from './agentConfigView'
import { UI_LEVELS, levelLabel } from './thoughtLevel'

/** composer 配置芯片的选项构建（#695 R4，纯函数，拆自
 * StudioChatComposerConfig 的文件预算）：把 agentConfigView 的视图模型
 * 翻译成 ConfigChip 的 Menu 项，值映射规则与原 StudioChatAgentConfigFields
 * 的 select 完全一致。 */

export type ChipOption = {
  value: string
  label: string
  title?: string
  current?: boolean
  disabled?: boolean
  header?: boolean
}

// off 关闭位在 MenuItem value 里的哨兵（原生 off value 各家不同，统一占位）。
export const THOUGHT_OFF_VALUE = '__off__'

export function modeOptions(modes: ModeView): ChipOption[] {
  return modes.available.map((mode) => ({
    value: mode.id,
    label: mode.name,
    title: mode.description,
    current: mode.id === modes.currentModeId,
  }))
}

export function modelOptions(entry: ConfigEntry): ChipOption[] {
  return entry.options.flatMap((option) =>
    isGroup(option)
      ? [
          { value: `group:${option.group}`, label: option.name, header: true },
          ...option.options.map((item) => ({
            value: item.value,
            label: item.name ?? item.value,
            title: item.description,
            current: item.value === entry.currentValue,
          })),
        ]
      : [
          {
            value: option.value,
            label: option.name ?? option.value,
            title: option.description,
            current: option.value === entry.currentValue,
          },
        ]
  )
}

export function thoughtOptions(thought: ThoughtView): ChipOption[] {
  const { map } = thought
  const options: ChipOption[] = []
  if (map.offValue !== null) {
    options.push({
      value: THOUGHT_OFF_VALUE,
      label: '关闭',
      current: map.current === 'off',
    })
  }
  for (const ui of UI_LEVELS) {
    const native = map.toNative[ui]
    if (native === undefined) continue
    options.push({
      value: ui,
      label: levelLabel(ui, native),
      current: map.current === ui,
    })
  }
  for (const value of map.unknownValues) {
    options.push({
      value,
      label: value,
      current: map.current === null && thought.currentValue === value,
    })
  }
  return options
}

/** 思考档芯片文本：「思考 high」/「思考 medium（→ low）」/「思考 关闭」/
 * 未知原生值原样。 */
export function thoughtText(thought: ThoughtView): string {
  const { map } = thought
  if (map.current === 'off') return '思考 关闭'
  if (map.current === null) return `思考 ${thought.currentValue}`
  return `思考 ${levelLabel(map.current, thought.currentValue)}`
}

/** 高级项的 pick 值编码为 `<configId>:<原生值>`，由调用方按首个冒号拆回。 */
export function advancedOptions(entries: ConfigEntry[]): ChipOption[] {
  return entries.flatMap((entry): ChipOption[] => {
    if (entry.type !== 'select') {
      return [
        {
          value: `entry:${entry.id}`,
          label: `${entry.name}：${entry.currentValue}（只读）`,
          disabled: true,
        },
      ]
    }
    return [
      { value: `entry:${entry.id}`, label: entry.name, header: true },
      ...flattenOptions(entry.options).map((option) => ({
        value: `${entry.id}:${option.value}`,
        label: option.name ?? option.value,
        title: option.description,
        current: option.value === entry.currentValue,
      })),
    ]
  })
}
