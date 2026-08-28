import { renderHook } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { useRuntimeModelOptions } from './runtimeModelOptions'

const RUNTIMES = {
  pi: {
    deepseek: ['v4-pro', 'v4-flash'],
    sqai: ['k2'],
  },
  '*': {
    // 通配声明：'*' provider / '*' model 是「任意值可 claim」的声明，
    // 不是可提交的字面选项，必须被过滤。
    '*': ['*'],
    gateway: ['*', 'g-1'],
  },
  velites: {
    sqai: ['k2', 'k2-mini'],
  },
}

describe('useRuntimeModelOptions', () => {
  it('merges the exact runtime with wildcard runtime entries and sorts', () => {
    const { result } = renderHook(() =>
      useRuntimeModelOptions(RUNTIMES, 'pi', 'deepseek')
    )
    expect(result.current.providerOptions).toEqual([
      'deepseek',
      'gateway',
      'sqai',
    ])
    expect(result.current.modelOptions).toEqual(['v4-flash', 'v4-pro'])
  })

  it('filters literal * provider and * model options', () => {
    const { result } = renderHook(() =>
      useRuntimeModelOptions(RUNTIMES, 'pi', 'gateway')
    )
    // gateway 声明了 '*' model：字面值不进选项，具体型号保留。
    expect(result.current.modelOptions).toEqual(['g-1'])
    // '*' provider 整体不进 provider 选项。
    expect(result.current.providerOptions).not.toContain('*')
  })

  it('lists every model of the runtime when no provider is selected', () => {
    const { result } = renderHook(() =>
      useRuntimeModelOptions(RUNTIMES, 'pi', '')
    )
    expect(result.current.modelOptions).toEqual([
      'g-1',
      'k2',
      'v4-flash',
      'v4-pro',
    ])
  })

  it('falls back to empty options when nothing matches', () => {
    const { result } = renderHook(() =>
      useRuntimeModelOptions(RUNTIMES, 'pi', 'unknown-provider')
    )
    expect(result.current.modelOptions).toEqual([])

    const empty = renderHook(() => useRuntimeModelOptions(undefined, 'pi', ''))
    expect(empty.result.current.providerOptions).toEqual([])
    expect(empty.result.current.modelOptions).toEqual([])
  })

  it('refreshes options when the runtime switches', () => {
    const { result, rerender } = renderHook(
      ({ runtime }) => useRuntimeModelOptions(RUNTIMES, runtime, ''),
      { initialProps: { runtime: 'pi' } }
    )
    expect(result.current.providerOptions).toEqual([
      'deepseek',
      'gateway',
      'sqai',
    ])

    rerender({ runtime: 'velites' })
    expect(result.current.providerOptions).toEqual(['gateway', 'sqai'])
    expect(result.current.modelOptions).toEqual(['g-1', 'k2', 'k2-mini'])
  })
})
