/** 通用思考档位映射（#368）：跨 agent 提供一套档位词，落到各家真实档位值上。
 *
 * 归一化档位序与 hermes-agent EFFORT_LADDER / openclaw THINKING_LEVEL_RANKS
 * 对齐；UI 通用档集 low/medium/high/max，off 是开关语义单独处理。clamp 采用
 * 单调就近策略：请求档在广告列表里→原样；不在→取 ≤ 它的最大档；无更弱档→
 * 列表最小档（GLM 式「下限就是 high」）。任何非 off 请求绝不落到 off；未知
 * value 不参与映射、原样透出高级设置。纯函数——每次 config 状态更新重算。 */

export const LADDER = [
  'off',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
  'ultra',
] as const
export type Level = (typeof LADDER)[number]
export const UI_LEVELS = ['low', 'medium', 'high', 'max'] as const
export type UiLevel = (typeof UI_LEVELS)[number]

const ALIASES: Record<string, Level> = {
  none: 'off',
  disabled: 'off',
  med: 'medium',
}

export function normalizeLevel(value: string): Level | null {
  const key = value.trim().toLowerCase()
  if ((LADDER as readonly string[]).includes(key)) return key as Level
  return ALIASES[key] ?? null
}

const rank = (level: Level) => LADDER.indexOf(level)

export type SelectValue = { value: string; name?: string; description?: string }

export type ThoughtLevelMap = {
  /** 通用档 → 该 agent 的原生 value（clamp 后）；known 为空时无条目。 */
  toNative: Partial<Record<UiLevel, string>>
  /** 当前原生值对应的通用档；关闭时为 'off'；未知值为 null。 */
  current: UiLevel | 'off' | null
  /** 广告列表里的关闭位原生 value（off/none/disabled），没有则 null。 */
  offValue: string | null
  /** 不可归一化的原生值——原样透出高级设置，不强行归一。 */
  unknownValues: string[]
  /** 单档（含零可选档）→ 控件降级为只读展示。 */
  readOnly: boolean
}

/** 通用档 X 落到的原生档：≤X 的最大已知档，否则已知最小档。 */
function clampToKnown(target: UiLevel, known: Level[]): Level | undefined {
  const weaker = known.filter((level) => rank(level) <= rank(target))
  return weaker.length > 0 ? weaker[weaker.length - 1] : known[0]
}

/** 原生档显示为哪个通用档：≤它的最大通用档（xhigh→high、ultra→max）；
 * 比 low 还弱的（minimal）显示为 low——不高于它的通用档不存在时取最小档。 */
function displayLevel(level: Level): UiLevel {
  const fits = UI_LEVELS.filter((ui) => rank(ui) <= rank(level))
  return fits.length > 0 ? fits[fits.length - 1] : UI_LEVELS[0]
}

export function buildThoughtLevelMap(
  currentValue: string,
  options: SelectValue[]
): ThoughtLevelMap {
  const byLevel = new Map<Level, string>()
  const unknownValues: string[] = []
  let offValue: string | null = null
  for (const option of options) {
    const level = normalizeLevel(option.value)
    if (level === null) unknownValues.push(option.value)
    else if (level === 'off') offValue ??= option.value
    else if (!byLevel.has(level)) byLevel.set(level, option.value)
  }
  const known = [...byLevel.keys()].sort((a, b) => rank(a) - rank(b))
  const toNative: Partial<Record<UiLevel, string>> = {}
  for (const ui of UI_LEVELS) {
    const level = clampToKnown(ui, known)
    if (level !== undefined) toNative[ui] = byLevel.get(level)
  }
  const currentLevel = normalizeLevel(currentValue)
  const current =
    currentLevel === null
      ? null
      : currentLevel === 'off'
        ? 'off'
        : displayLevel(currentLevel)
  return {
    toNative,
    current,
    offValue,
    unknownValues,
    readOnly: known.length + (offValue === null ? 0 : 1) <= 1,
  }
}
