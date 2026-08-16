import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStudioRightPanelTab } from './useStudioRightPanelTab'

describe('useStudioRightPanelTab', () => {
  it('switches back to the inspector tab when a node is selected', () => {
    const setSelectedNodeKey = vi.fn()
    const { result } = renderHook(() =>
      useStudioRightPanelTab(setSelectedNodeKey)
    )

    act(() => result.current.setTab('chat'))
    expect(result.current.tab).toBe('chat')
    expect(result.current.chatOpen).toBe(true)

    // chat 里点「查看草稿」走的必须是这条路径：选中节点并自动切回节点配置
    // tab，否则从用户视角像没反应（#91）。
    act(() => result.current.selectNode('node-1'))
    expect(setSelectedNodeKey).toHaveBeenCalledWith('node-1')
    expect(result.current.tab).toBe('inspector')
  })

  it('keeps the current tab when the selection is cleared', () => {
    const setSelectedNodeKey = vi.fn()
    const { result } = renderHook(() =>
      useStudioRightPanelTab(setSelectedNodeKey)
    )

    act(() => result.current.setTab('chat'))
    act(() => result.current.selectNode(null))
    expect(setSelectedNodeKey).toHaveBeenCalledWith(null)
    expect(result.current.tab).toBe('chat')
  })
})
