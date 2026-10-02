/**
 * dockStack 纯逻辑测试（#801 codex P1/轮 2，node 环境）：入栈/置顶/出栈、
 * Esc 栈顶判定、栈位→z-index 映射与 999 上限（低于 Toast）。
 */
import { describe, it, expect, vi } from 'vitest'
import {
  dockStackIsTop,
  dockStackRaise,
  dockStackRemove,
  dockStackSubscribe,
  dockStackZIndex,
} from './dockStack'

describe('dockStack', () => {
  it('raise 置顶、remove 出栈、isTop 读栈顶', () => {
    const a = Symbol('a')
    const b = Symbol('b')
    dockStackRaise(a)
    dockStackRaise(b)
    expect(dockStackIsTop(b)).toBe(true)
    expect(dockStackIsTop(a)).toBe(false)
    // 重复 raise 幂等置顶。
    dockStackRaise(a)
    expect(dockStackIsTop(a)).toBe(true)
    dockStackRemove(a)
    expect(dockStackIsTop(a)).toBe(false)
    expect(dockStackIsTop(b)).toBe(true)
    dockStackRemove(b)
  })

  it('栈位映射 z-index = 900 + 栈位；不在栈内回 900', () => {
    const a = Symbol('a')
    const b = Symbol('b')
    dockStackRaise(a)
    dockStackRaise(b)
    expect(dockStackZIndex(a)).toBe(900)
    expect(dockStackZIndex(b)).toBe(901)
    dockStackRaise(a)
    expect(dockStackZIndex(a)).toBe(901)
    expect(dockStackZIndex(b)).toBe(900)
    dockStackRemove(a)
    dockStackRemove(b)
    expect(dockStackZIndex(Symbol('never'))).toBe(900)
  })

  it('栈深超过上限时 z-index 钳在 999（低于 Toast）', () => {
    const ids: symbol[] = []
    for (let i = 0; i < 105; i += 1) {
      const id = Symbol(`d${i}`)
      ids.push(id)
      dockStackRaise(id)
    }
    expect(dockStackZIndex(ids[99])).toBe(999)
    expect(dockStackZIndex(ids[104])).toBe(999)
    for (const id of ids) dockStackRemove(id)
  })

  it('栈变动通知订阅者（useSyncExternalStore 的 subscribe 契约）', () => {
    const listener = vi.fn()
    const unsubscribe = dockStackSubscribe(listener)
    const id = Symbol('x')
    dockStackRaise(id)
    expect(listener).toHaveBeenCalledTimes(1)
    dockStackRemove(id)
    expect(listener).toHaveBeenCalledTimes(2)
    unsubscribe()
    dockStackRaise(id)
    expect(listener).toHaveBeenCalledTimes(2)
    dockStackRemove(id)
  })
})
