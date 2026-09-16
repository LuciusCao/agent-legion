import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'

describe('useWorkflowStudioMobilePanel', () => {
  it('defaults to graph panel', () => {
    const { result } = renderHook(() => useWorkflowStudioMobilePanel(null))
    expect(result.current.mobilePanel).toBe('graph')
  })

  it('switches to editor when a node is selected', () => {
    const { result, rerender } = renderHook(
      ({ selectedNodeKey }: { selectedNodeKey: string | null }) =>
        useWorkflowStudioMobilePanel(selectedNodeKey),
      { initialProps: { selectedNodeKey: null as string | null } }
    )

    rerender({ selectedNodeKey: 'node-a' })
    expect(result.current.mobilePanel).toBe('editor')

    rerender({ selectedNodeKey: null })
    expect(result.current.mobilePanel).toBe('graph')
  })

  it('switches to editor again on focus nonce bump with the same key (#667)', () => {
    // 移动端在 Agent 面板点草稿 diff 里已选中的同一节点：选中值不变，
    // 仅靠 key 变化不会切回编辑面板；定位请求 nonce bump 必须触发。
    const { result, rerender } = renderHook(
      ({
        selectedNodeKey,
        focusNonce,
      }: {
        selectedNodeKey: string | null
        focusNonce: number
      }) => useWorkflowStudioMobilePanel(selectedNodeKey, focusNonce),
      { initialProps: { selectedNodeKey: 'node-a', focusNonce: 0 } }
    )
    expect(result.current.mobilePanel).toBe('editor')

    // 用户手动切到 Agent 面板（选中值不变）。
    act(() => result.current.setMobilePanel('agent'))
    expect(result.current.mobilePanel).toBe('agent')

    rerender({ selectedNodeKey: 'node-a', focusNonce: 1 })
    expect(result.current.mobilePanel).toBe('editor')
  })
})
