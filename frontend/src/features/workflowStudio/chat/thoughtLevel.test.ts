import { describe, expect, it } from 'vitest'
import {
  LADDER,
  UI_LEVELS,
  buildThoughtLevelMap,
  normalizeLevel,
} from './thoughtLevel'

// issue #368 调研里各家 agent 的真实广告值作 fixture。
const KIMI = ['low', 'high', 'max'].map((value) => ({ value }))
const CODEX_GPT56 = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'].map(
  (value) => ({ value })
)
const GOOSE = ['off', 'low', 'medium', 'high', 'max'].map((value) => ({
  value,
}))
const GLM = ['high', 'max'].map((value) => ({ value }))

const rankOf = (value: string) => LADDER.indexOf(normalizeLevel(value)!)

describe('normalizeLevel', () => {
  it('accepts the ladder and the alias table, rejects unknown values', () => {
    expect(normalizeLevel('High')).toBe('high')
    expect(normalizeLevel('none')).toBe('off')
    expect(normalizeLevel('disabled')).toBe('off')
    expect(normalizeLevel('med')).toBe('medium')
    expect(normalizeLevel('turbo')).toBeNull()
  })
})

describe('buildThoughtLevelMap', () => {
  it('kimi: gapped ladder — medium clamps to the nearest weaker (low), pinned', () => {
    const map = buildThoughtLevelMap('high', KIMI)
    expect(map.toNative).toEqual({
      low: 'low',
      medium: 'low',
      high: 'high',
      max: 'max',
    })
    expect(map.current).toBe('high')
    expect(map.offValue).toBeNull()
    expect(map.readOnly).toBe(false)
  })

  it('codex: xhigh displays as high, ultra as max; ui picks stay in the ui set', () => {
    expect(buildThoughtLevelMap('xhigh', CODEX_GPT56).current).toBe('high')
    expect(buildThoughtLevelMap('ultra', CODEX_GPT56).current).toBe('max')
    expect(buildThoughtLevelMap('medium', CODEX_GPT56).toNative).toEqual({
      low: 'low',
      medium: 'medium',
      high: 'high',
      max: 'max',
    })
  })

  it('goose: off is an independent switch, never a downgrade target', () => {
    const map = buildThoughtLevelMap('off', GOOSE)
    expect(map.offValue).toBe('off')
    expect(map.current).toBe('off')
    for (const ui of UI_LEVELS) expect(map.toNative[ui]).not.toBe('off')
    // off is never resolved for a non-off request even on the weakest pick.
    expect(map.toNative.low).toBe('low')
  })

  it('GLM-style floor: every request at or below the floor resolves to the floor', () => {
    const map = buildThoughtLevelMap('high', GLM)
    expect(map.toNative).toEqual({
      low: 'high',
      medium: 'high',
      high: 'high',
      max: 'max',
    })
  })

  it('is monotonic for every fixture: X<Y ⇒ resolve(X) ≤ resolve(Y)', () => {
    for (const options of [KIMI, CODEX_GPT56, GOOSE, GLM]) {
      const map = buildThoughtLevelMap(options[0].value, options)
      for (let i = 0; i < UI_LEVELS.length - 1; i += 1) {
        const lower = map.toNative[UI_LEVELS[i]]!
        const higher = map.toNative[UI_LEVELS[i + 1]]!
        expect(rankOf(lower)).toBeLessThanOrEqual(rankOf(higher))
      }
    }
  })

  it('single-level lists (goose non-reasoning model) degrade to read-only', () => {
    const map = buildThoughtLevelMap('off', [{ value: 'off' }])
    expect(map.readOnly).toBe(true)
    expect(map.toNative).toEqual({})
    expect(buildThoughtLevelMap('high', [{ value: 'high' }]).readOnly).toBe(
      true
    )
  })

  it('unknown values pass through untouched instead of being normalized', () => {
    const map = buildThoughtLevelMap('turbo', [...KIMI, { value: 'turbo' }])
    expect(map.unknownValues).toEqual(['turbo'])
    expect(map.current).toBeNull()
    expect(map.toNative.max).toBe('max')
  })

  it('an all-unknown list with more than one value stays selectable, not read-only', () => {
    const map = buildThoughtLevelMap('a', [{ value: 'a' }, { value: 'b' }])
    expect(map.readOnly).toBe(false)
    expect(map.unknownValues).toEqual(['a', 'b'])
    expect(map.toNative).toEqual({})
  })

  it('minimal (weaker than low) displays as low, not as off', () => {
    const map = buildThoughtLevelMap('minimal', [
      { value: 'minimal' },
      { value: 'medium' },
    ])
    expect(map.current).toBe('low')
    expect(map.toNative.low).toBe('minimal')
  })
})
