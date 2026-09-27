/**
 * AgentPanelDock 的焦点管理（issue #795 PR①，沿用 #790 overlayChrome 的
 * 契约）：打开/展开时焦点进面板 surface，折叠时焦点移到右下角展开小条
 * （折叠会把含焦点的内容区切为 display:none，不移交则键盘用户丢失上下文），
 * 卸载时还原触发元素。preventScroll 防焦点驱动的页面跳动。
 * hidden（#797 codex P2）与折叠分开处理：hidden 连小条都不渲染——若当折叠
 * 处理去聚焦 chipRef（不存在），焦点会留在 display:none 子树或裸丢 body。
 * 归还目标 = 面板外最后聚焦的可见元素（Dock 可见期间 focusin 持续追踪，
 * 面板内/小条上的焦点不记），兜底挂载前的焦点元素——常驻 Dock 的隐藏多由
 * 顶栏开关/页签触发，焦点还给刚点的那个控件（#797 复审轮：只在挂载时记
 * 的 previous 通常是 body，常驻场景下已过期）。
 * ref 用 callback ref + state 而非 useRef：react-rnd 挂载期 componentDidMount
 * 内 setState/forceUpdate 触发嵌套重渲染，首帧 passive effect 里 useRef 的
 * current 可能仍是 null（实测），callback ref 的 node 到位通知才可靠。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { Dispatch, RefCallback, SetStateAction } from 'react'

export interface DockFocus {
  surfaceRef: RefCallback<HTMLDivElement>
  chipRef: RefCallback<HTMLButtonElement>
}

/** callback ref 工厂：node 到位写 state（react-rnd 挂载期首帧 ref 可能仍
 * 为 null，state 通知才可靠）。 */
function useNodeRef<T extends HTMLElement>(
  set: Dispatch<SetStateAction<T | null>>
) {
  return useCallback((node: T | null) => set(node), [set])
}

function focusIfConnected(el: Element | null) {
  if (el instanceof HTMLElement && el.isConnected)
    el.focus({ preventScroll: true })
}

export function useDockFocus(collapsed: boolean, hidden = false): DockFocus {
  const [surfaceNode, setSurfaceNode] = useState<HTMLDivElement | null>(null)
  const [chipNode, setChipNode] = useState<HTMLButtonElement | null>(null)
  const surfaceRef = useNodeRef(setSurfaceNode)
  const chipRef = useNodeRef(setChipNode)
  // 挂载前的焦点元素：卸载还原的兜底目标。
  const mountPreviousRef = useRef<Element | null>(null)
  // 面板外最后聚焦的可见元素：hidden 转换的归还目标（见文件头注释）。
  const outsideRef = useRef<Element | null>(null)

  useEffect(() => {
    mountPreviousRef.current = document.activeElement
    return () => focusIfConnected(mountPreviousRef.current)
  }, [])

  // Dock 可见期间追踪面板外焦点（focusin）：面板内/小条上的焦点不记——
  // 隐藏/折叠时焦点本就该离开面板，记了反而还回不可见子树。hidden 期间
  // 也照记（还回目标被聚焦同样是「面板外最后聚焦」）。
  useEffect(() => {
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target as Element | null
      if (
        !target ||
        surfaceNode?.contains(target) ||
        chipNode?.contains(target)
      )
        return
      outsideRef.current = target
    }
    document.addEventListener('focusin', onFocusIn)
    return () => document.removeEventListener('focusin', onFocusIn)
  }, [surfaceNode, chipNode])

  useEffect(() => {
    if (hidden) {
      focusIfConnected(outsideRef.current ?? mountPreviousRef.current)
      return
    }
    const node = collapsed ? chipNode : surfaceNode
    node?.focus({ preventScroll: true })
  }, [collapsed, hidden, surfaceNode, chipNode])

  return { surfaceRef, chipRef }
}
