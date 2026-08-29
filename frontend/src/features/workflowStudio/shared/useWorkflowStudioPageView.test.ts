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
})
