import { createContext, useContext } from 'react'
import type { useWorkflowStudio } from './useWorkflowStudio'
import type { useWorkflowStudioPageView } from './useWorkflowStudioPageView'

// Studio 状态分发通道：页面层把 useWorkflowStudio() / useWorkflowStudioPageView()
// 的返回值放进两个 context，深层组件（Canvas/Chat/Detail/Dialogs 等）直接按需
// 消费，替代原先 Layout → Workspace → SplitLayout → CanvasPanel/ChatAside 的
// 整包 props 钻孔。value 即 hook 返回值本身（组件层不重建对象），渲染语义与
// 原先 props 穿透完全一致：对象引用变化 → 消费者重渲染，与字段无关。
export type StudioState = ReturnType<typeof useWorkflowStudio>
export type StudioView = ReturnType<typeof useWorkflowStudioPageView>

// null 语义：宿主未挂载 Provider（测试直接渲染深层组件时会出现）。
// 消费方一律经 useStudioState()/useStudioView() 拿值，禁止直接 useContext。
export const StudioStateContext = createContext<StudioState | null>(null)
export const StudioViewContext = createContext<StudioView | null>(null)

export function useStudioState(): StudioState {
  const studio = useContext(StudioStateContext)
  if (studio === null) {
    throw new Error('useStudioState requires StudioStateContext.Provider')
  }
  return studio
}

export function useStudioView(): StudioView {
  const view = useContext(StudioViewContext)
  if (view === null) {
    throw new Error('useStudioView requires StudioViewContext.Provider')
  }
  return view
}
