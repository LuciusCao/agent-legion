import type { ConfigEntry, ModeView, ThoughtView } from './agentConfigView'
import { flattenOptions, isGroup } from './agentConfigView'
import { UI_LEVELS, levelLabel } from './thoughtLevel'

/** composer 配置芯片的选项构建（#695 R4，纯函数，拆自
 * StudioChatComposerConfig 的文件预算）：把 agentConfigView 的视图模型
 * 翻译成 ConfigChip 的 Menu 项，值映射规则与原 StudioChatAgentConfigFields
 * 的 select 完全一致。
 * 提交一律走结构化 `submit`（configId/value 分开携带，闭包数据）：#733 R4-P2
 * ——后端契约只要求 config id 非空、不禁止冒号，此前高级项把 id:value 拼成
 * 字符串再按首个冒号拆回，id 含冒号时被截成错误前缀遭 `Unknown config
 * option` 拒绝；字符串编码同样被「未知原生值恰等于哨兵」这类碰撞威胁。 */

export type ChipOption = {
  /** 仅作 React key / 菜单标识，永不参与解析拆回。 */
  value: string
  label: string
  title?: string
  current?: boolean
  disabled?: boolean
  header?: boolean
  /** 结构化提交载荷；header / 只读项没有（不可点或点了不提交）。 */
  submit?: { configId: string; value: string }
}

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
            submit: { configId: entry.id, value: item.value },
          })),
        ]
      : [
          {
            value: option.value,
            label: option.name ?? option.value,
            title: option.description,
            current: option.value === entry.currentValue,
            submit: { configId: entry.id, value: option.value },
          },
        ]
  )
}

export function thoughtOptions(thought: ThoughtView): ChipOption[] {
  const { map } = thought
  const options: ChipOption[] = []
  if (map.offValue !== null) {
    options.push({
      value: 'off',
      label: '关闭',
      current: map.current === 'off',
      submit: { configId: thought.id, value: map.offValue },
    })
  }
  for (const ui of UI_LEVELS) {
    const native = map.toNative[ui]
    if (native === undefined) continue
    // 通用档走映射；提交的是原生值，不是档位词。
    options.push({
      value: `ui:${ui}`,
      label: levelLabel(ui, native),
      current: map.current === ui,
      submit: { configId: thought.id, value: native },
    })
  }
  for (const value of map.unknownValues) {
    // 未知原生值原样透传（看得见、选得到、切走后切得回）。
    options.push({
      value: `native:${value}`,
      label: value,
      current: map.current === null && thought.currentValue === value,
      submit: { configId: thought.id, value },
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
        value: `entry:${entry.id}:${option.value}`,
        label: option.name ?? option.value,
        title: option.description,
        current: option.value === entry.currentValue,
        submit: { configId: entry.id, value: option.value },
      })),
    ]
  })
}
