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

  // #797 codex 复审轮 2：窄屏开关以 Dock **实际可见性**（agentOpen &&
  // 页签=agent）为真值——agentOpen 与页签脱节时按 agentOpen 翻转要点两次
  // 才生效；按可见性切换则一次到位。
  it('narrow viewport: toggle keys off actual dock visibility, one click lands', () => {
    stubNarrowViewport(true)
    try {
      const { result } = renderHook(() =>
        useWorkflowStudioPageView(
          makeStudio() as unknown as Parameters<
            typeof useWorkflowStudioPageView
          >[0]
        )
      )

      // 窄屏初始：agentOpen=true 但页签在画布 → 实际不可见（脱节态）。
      expect(result.current.agentOpen).toBe(true)
      expect(result.current.dockVisible).toBe(false)

      // 第一次点击即生效：打开 + 切 Agent 页签（不再要点两次）。
      act(() => result.current.toggleAgent())
      expect(result.current.dockVisible).toBe(true)
      expect(result.current.mobilePanel).toBe('agent')

      // 可见时点击：关闭 + 回画布页签（不留空白工作区）。
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(false)
      expect(result.current.dockVisible).toBe(false)
      expect(result.current.mobilePanel).toBe('graph')

      // 从 Agent 页签切走画布进入同一脱节态：再点一次即打开。
      act(() => result.current.setMobilePanel('agent'))
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(true)
      act(() => result.current.setMobilePanel('graph'))
      expect(result.current.dockVisible).toBe(false)
      act(() => result.current.toggleAgent())
      expect(result.current.dockVisible).toBe(true)
      expect(result.current.mobilePanel).toBe('agent')
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
