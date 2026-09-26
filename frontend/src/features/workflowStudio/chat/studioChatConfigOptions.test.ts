import { describe, expect, it } from 'vitest'
import { advancedOptions, thoughtOptions } from './studioChatConfigOptions'
import type { ConfigEntry, ThoughtView } from './agentConfigView'
import { buildThoughtLevelMap } from './thoughtLevel'

function entry(overrides: Partial<ConfigEntry>): ConfigEntry {
  return {
    id: 'x',
    name: 'X',
    type: 'select',
    currentValue: 'v',
    options: [],
    ...overrides,
  }
}

describe('studioChatConfigOptions', () => {
  it('generates collision-free keys for colon-containing ids (#733 R7-P2-a)', () => {
    // 旧编码下 id="a:b" 的组头与 id="a" 的 value="b" 选项拼出同一个
    // key（entry:a:b）；key 改为构建期序号后同级必唯一。
    const options = advancedOptions([
      entry({ id: 'a:b', options: [{ value: 'v' }] }),
      entry({ id: 'a', options: [{ value: 'b' }] }),
    ])
    expect(options).toHaveLength(4) // 两组头 + 两选项
    expect(new Set(options.map((o) => o.key)).size).toBe(4)
    // 提交载荷不受 key 影响：结构化 configId/value 原样携带。
    expect(options[3].submit).toEqual({ configId: 'a', value: 'b' })
  })

  it('attaches structured submit payloads on thought options (off / ui / unknown)', () => {
    const thought: ThoughtView = {
      ...entry({
        id: 'thinking',
        currentValue: 'high',
        options: [
          { value: 'none' },
          { value: 'low' },
          { value: 'high' },
          { value: 'turbo' },
        ],
      }),
      map: buildThoughtLevelMap('high', [
        { value: 'none' },
        { value: 'low' },
        { value: 'high' },
        { value: 'turbo' },
      ]),
    }
    const byLabel = new Map(thoughtOptions(thought).map((o) => [o.label, o]))
    expect(byLabel.get('关闭')?.submit).toEqual({
      configId: 'thinking',
      value: 'none',
    })
    expect(byLabel.get('low')?.submit).toEqual({
      configId: 'thinking',
      value: 'low',
    })
    expect(byLabel.get('turbo')?.submit).toEqual({
      configId: 'thinking',
      value: 'turbo',
    })
  })
})
