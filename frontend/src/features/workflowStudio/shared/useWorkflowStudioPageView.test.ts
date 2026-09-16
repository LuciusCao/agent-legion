import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useWorkflowStudioPageView } from './useWorkflowStudioPageView'

function makeStudio() {
  return { validateDraft: vi.fn().mockResolvedValue(undefined) }
}

describe('useWorkflowStudioPageView', () => {
  it('opens the changes panel after validation instead of switching modes', async () => {
    const studio = makeStudio()
    const { result } = renderHook(() =>
      // 伪造对象只覆盖 hook 消费的字段。
      useWorkflowStudioPageView(
        studio as unknown as Parameters<typeof useWorkflowStudioPageView>[0]
      )
    )

    expect(result.current.changesPanelOpen).toBe(false)
    await act(() => result.current.validateAndShowResult())

    expect(studio.validateDraft).toHaveBeenCalledTimes(1)
    expect(result.current.changesPanelOpen).toBe(true)
  })

  it('tracks changes panel and YAML editor open state independently', () => {
    const { result } = renderHook(() =>
      useWorkflowStudioPageView(
        makeStudio() as unknown as Parameters<
          typeof useWorkflowStudioPageView
        >[0]
      )
    )

    act(() => result.current.setYamlEditorOpen(true))
    expect(result.current.yamlEditorOpen).toBe(true)
    expect(result.current.changesPanelOpen).toBe(false)

    act(() => result.current.setChangesPanelOpen(true))
    expect(result.current.changesPanelOpen).toBe(true)
    expect(result.current.yamlEditorOpen).toBe(true)
  })

  // #668：agentOpen 提升到 page view 层，appbar 开关与分栏布局共享此状态。
  it('toggles the agent panel open state', () => {
    const { result } = renderHook(() =>
      useWorkflowStudioPageView(
        makeStudio() as unknown as Parameters<
          typeof useWorkflowStudioPageView
        >[0]
      )
    )

    expect(result.current.agentOpen).toBe(true)
    act(() => result.current.toggleAgent())
    expect(result.current.agentOpen).toBe(false)
    act(() => result.current.toggleAgent())
    expect(result.current.agentOpen).toBe(true)
  })
})
