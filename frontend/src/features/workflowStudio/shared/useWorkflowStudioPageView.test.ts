import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useWorkflowStudioPageView } from './useWorkflowStudioPageView'

/** 窄屏判定桩（useStudioNarrowViewport 走 matchMedia，node/jsdom 环境没有）。
 * 可翻转：change 监听共享同一集合，setNarrowViewport 触发断点切换。 */
const originalMatchMedia = globalThis.matchMedia
let narrowMatches = false
const narrowListeners = new Set<() => void>()

function stubNarrowViewport(matches: boolean) {
  narrowMatches = matches
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      get matches() {
        return narrowMatches
      },
      media: query,
      onchange: null,
      addEventListener: (_type: string, cb: () => void) =>
        narrowListeners.add(cb),
      removeEventListener: (_type: string, cb: () => void) =>
        narrowListeners.delete(cb),
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  })
}

function setNarrowViewport(next: boolean) {
  narrowMatches = next
  narrowListeners.forEach((cb) => cb())
}

function restoreViewportMatchMedia() {
  narrowListeners.clear()
  Object.defineProperty(globalThis, 'matchMedia', {
    writable: true,
    configurable: true,
    value: originalMatchMedia,
  })
}

describe('useWorkflowStudioPageView', () => {
  it('tracks changes panel and YAML editor open state independently', () => {
    const { result } = renderHook(() => useWorkflowStudioPageView())

    act(() => result.current.setYamlEditorOpen(true))
    expect(result.current.yamlEditorOpen).toBe(true)
    expect(result.current.changesPanelOpen).toBe(false)

    act(() => result.current.setChangesPanelOpen(true))
    expect(result.current.changesPanelOpen).toBe(true)
    expect(result.current.yamlEditorOpen).toBe(true)
  })

  // #668：agentOpen 提升到 page view 层，appbar 开关与分栏布局共享此状态。
  it('toggles the agent panel open state', () => {
    const { result } = renderHook(() => useWorkflowStudioPageView())

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
      const { result } = renderHook(() => useWorkflowStudioPageView())

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
      const { result } = renderHook(() => useWorkflowStudioPageView())
      act(() => result.current.setMobilePanel('agent'))
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(false)
      // 宽屏不动 mobilePanel。
      expect(result.current.mobilePanel).toBe('agent')
    } finally {
      restoreViewportMatchMedia()
    }
  })

  // #797 codex 复审轮 4：跨断点规范化——宽屏关 Dock 只翻 agentOpen，潜伏
  // 的 agent 页签进入窄屏时归位画布（否则选中空内容页：Dock 隐藏、画布/
  // 编辑器被响应式 CSS 隐藏）。
  it('cross-breakpoint: latent agent tab normalizes to graph when entering narrow with dock closed', () => {
    stubNarrowViewport(true)
    try {
      const { result } = renderHook(() => useWorkflowStudioPageView())

      // 四步复现：窄屏开 Agent 页签（初始脱节 agentOpen=true+graph →
      // 一次点击打开并切页签）。
      act(() => result.current.toggleAgent())
      expect(result.current.dockVisible).toBe(true)
      expect(result.current.mobilePanel).toBe('agent')

      // 拉宽桌面 → 顶栏关 Dock：宽屏分支只翻 agentOpen，页签潜伏 agent。
      act(() => setNarrowViewport(false))
      act(() => result.current.toggleAgent())
      expect(result.current.agentOpen).toBe(false)
      expect(result.current.mobilePanel).toBe('agent')

      // 缩回窄屏：规范化归位画布——画布可见，不留空白工作区（revert 即红：
      // 无归位 effect 时 mobilePanel 滞留 agent）。
      act(() => setNarrowViewport(true))
      expect(result.current.mobilePanel).toBe('graph')
      expect(result.current.dockVisible).toBe(false)
    } finally {
      restoreViewportMatchMedia()
    }
  })
})
