import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useWorkflowStudioPageView } from './useWorkflowStudioPageView'

function makeStudio() {
  return { validateDraft: vi.fn().mockResolvedValue(undefined) }
}

/** 窄屏判定桩（useStudioNarrowViewport 走 matchMedia，node/jsdom 环境没有）。 */
const originalMatchMedia = globalThis.matchMedia

function stubNarrowViewport(matches: boolean) {
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  })
}

function restoreViewportMatchMedia() {
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    configurable: true,
    value: originalMatchMedia,
  })
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

  // #797 codex 复审轮：toggleAgent 是开合的唯一组合出口——宽屏不动页签；
  // 窄屏打开切 Agent 页签、从 Agent 页签关闭回画布。
  it('narrow viewport: toggleAgent syncs the mobile tab both directions', () => {
    stubNarrowViewport(true)
    try {
      const { result } = renderHook(() =>
        useWorkflowStudioPageView(
          makeStudio() as unknown as Parameters<
            typeof useWorkflowStudioPageView
          >[0]
        )
      )

      // 窄屏画布页签 + 默认开：初始 agentOpen=true 时不动页签（默认态不抢）。
      // 先关：Agent 页签…初始 mobilePanel=graph，关闭不发生在 agent 页签→不动。
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(false)
      expect(result.current.mobilePanel).toBe('graph')

      // 窄屏打开（当前画布页签）：切到 Agent 页签让 Dock 可见。
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(true)
      expect(result.current.mobilePanel).toBe('agent')

      // 窄屏从 Agent 页签关闭：回画布，不留空白工作区。
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(false)
      expect(result.current.mobilePanel).toBe('graph')
    } finally {
      restoreViewportMatchMedia()
    }
  })

  it('wide viewport: toggleAgent leaves the mobile tab untouched', () => {
    stubNarrowViewport(false)
    try {
      const { result } = renderHook(() =>
        useWorkflowStudioPageView(
          makeStudio() as unknown as Parameters<
            typeof useWorkflowStudioPageView
          >[0]
        )
      )
      act(() => result.current.setMobilePanel('agent'))
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(false)
      // 宽屏不动 mobilePanel。
      expect(result.current.mobilePanel).toBe('agent')
    } finally {
      restoreViewportMatchMedia()
    }
  })
})
