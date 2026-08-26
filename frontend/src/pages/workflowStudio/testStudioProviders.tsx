import type { ReactNode } from 'react'
import { vi } from 'vitest'
import { StudioStateContext, StudioViewContext } from './studioStateContext'
import type { StudioState, StudioView } from './studioStateContext'

// 测试助手：把 studio（hook 返回值形态的伪造对象）与 view 挂进
// StudioStateContext/StudioViewContext 后渲染子树。Layout 及以下组件
// 不再接收整包 props，测试从这里注入状态。
export function withStudioProviders(
  studio: object,
  view: object,
  children: ReactNode
) {
  return (
    <StudioStateContext.Provider value={studio as StudioState}>
      <StudioViewContext.Provider value={view as StudioView}>
        {children}
      </StudioViewContext.Provider>
    </StudioStateContext.Provider>
  )
}

/** view 字段的默认形状（useWorkflowStudioPageView 的返回值）。 */
export function makeStudioView(overrides: Record<string, unknown> = {}) {
  return {
    dagFullscreenOpen: false,
    setDagFullscreenOpen: vi.fn(),
    canvasMode: 'dag' as const,
    setCanvasMode: vi.fn(),
    validateAndShowResult: vi.fn(),
    ...overrides,
  }
}
