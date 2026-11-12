import { describe, expect, it } from 'vitest'
import { configOverrideValueOf } from './ConfigValueField'

describe('configOverrideValueOf (#1258 复验 P3-B)', () => {
  it('ignores Object.prototype keys when reading overrides', () => {
    // `key in liveOverrides` 走原型链：schema 属性名为 toString 时，无该
    // 自有键的 overrides 会读到内建方法体并当成 override 值展示。
    expect(configOverrideValueOf({}, 'toString')).toBeUndefined()
    expect(configOverrideValueOf(undefined, 'toString')).toBeUndefined()
    // 自有键正常返回（含显式 undefined 以外的值）。
    expect(configOverrideValueOf({ toString: 'x' }, 'toString')).toEqual({
      value: 'x',
    })
  })
})
