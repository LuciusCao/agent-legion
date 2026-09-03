import type { StudioChatSessionRecord } from './studioChatApi'
import {
  buildThoughtLevelMap,
  type SelectValue,
  type ThoughtLevelMap,
} from './thoughtLevel'

/** 会话行里 agent 广告的配置面 → 面板视图模型（#368）。通用选择器挂在
 * category（model / thought_level）上而不是 configId 上——同一语义三家三个
 * id；未知 category（含 `_` 前缀自定义）与 boolean 型折叠进高级设置。 */

export type ModeOption = { id: string; name: string; description?: string }
export type ModeView = { currentModeId: string; available: ModeOption[] }
export type OptionGroup = {
  group: string
  name: string
  options: SelectValue[]
}
export type ConfigEntry = {
  id: string
  name: string
  description?: string
  category?: string
  type: string
  currentValue: string
  options: (SelectValue | OptionGroup)[]
}
export type ThoughtView = ConfigEntry & { map: ThoughtLevelMap }
export type AgentConfigView = {
  visible: boolean
  modes: ModeView | null
  model: ConfigEntry | null
  thought: ThoughtView | null
  advanced: ConfigEntry[]
}

type Rec = Record<string, unknown>
const asRec = (x: unknown): Rec | null =>
  x !== null && typeof x === 'object' && !Array.isArray(x) ? (x as Rec) : null
const str = (x: unknown): string =>
  typeof x === 'string' ? x : String(x ?? '')
const optStr = (x: unknown): string | undefined =>
  typeof x === 'string' ? x : undefined

export const isGroup = (
  option: SelectValue | OptionGroup
): option is OptionGroup => 'group' in option

/** 分组列表拍平成值列表（映射与白名单都只认值）。 */
export function flattenOptions(
  options: (SelectValue | OptionGroup)[]
): SelectValue[] {
  return options.flatMap((option) =>
    isGroup(option) ? option.options : [option]
  )
}

function parseOption(raw: unknown): SelectValue | OptionGroup | null {
  const rec = asRec(raw)
  if (!rec) return null
  if ('group' in rec) {
    const nested = Array.isArray(rec.options) ? rec.options : []
    return {
      group: str(rec.group),
      name: str(rec.name),
      options: nested
        .map(parseOption)
        .filter((o): o is SelectValue => !!o && !isGroup(o)),
    }
  }
  if (!('value' in rec)) return null
  return {
    value: str(rec.value),
    name: optStr(rec.name),
    description: optStr(rec.description),
  }
}

function parseEntry(raw: unknown): ConfigEntry | null {
  const rec = asRec(raw)
  if (!rec || typeof rec.id !== 'string') return null
  const options = Array.isArray(rec.options) ? rec.options : []
  return {
    id: rec.id,
    name: optStr(rec.name) ?? rec.id,
    description: optStr(rec.description),
    category: optStr(rec.category),
    type: str(rec.type),
    currentValue: str(rec.currentValue),
    options: options
      .map(parseOption)
      .filter((o): o is SelectValue | OptionGroup => o !== null),
  }
}

function parseModes(raw: unknown): ModeView | null {
  const rec = asRec(raw)
  if (!rec || !Array.isArray(rec.availableModes)) return null
  const available = rec.availableModes
    .map(asRec)
    .filter((m): m is Rec => m !== null && typeof m.id === 'string')
    .map((m) => ({
      id: str(m.id),
      name: optStr(m.name) ?? str(m.id),
      description: optStr(m.description),
    }))
  return available.length > 0
    ? { currentModeId: str(rec.currentModeId), available }
    : null
}

export function agentConfigView(
  session: StudioChatSessionRecord | null
): AgentConfigView {
  const empty: AgentConfigView = {
    visible: false,
    modes: null,
    model: null,
    thought: null,
    advanced: [],
  }
  if (!session) return empty
  const snapshot = session.capability_snapshot ?? {}
  // 不广告两类能力的 agent（claude-code-acp、旧版 kimi）配置区整体隐藏。
  if (snapshot.sessionModes !== true && snapshot.sessionConfigOptions !== true)
    return empty
  const modes = parseModes(session.session_modes)
  const entries = (session.config_options ?? [])
    .map(parseEntry)
    .filter((e): e is ConfigEntry => e !== null)
  let model: ConfigEntry | null = null
  let thought: ThoughtView | null = null
  const advanced: ConfigEntry[] = []
  for (const entry of entries) {
    if (entry.type === 'select' && entry.category === 'model' && !model)
      model = entry
    else if (
      entry.type === 'select' &&
      entry.category === 'thought_level' &&
      !thought
    )
      // 理论上可能多条，取第一条并忽略其余（issue #368 设计）。
      thought = {
        ...entry,
        map: buildThoughtLevelMap(
          entry.currentValue,
          flattenOptions(entry.options)
        ),
      }
    else advanced.push(entry)
  }
  const visible =
    modes !== null || model !== null || thought !== null || advanced.length > 0
  return { visible, modes, model, thought, advanced }
}
