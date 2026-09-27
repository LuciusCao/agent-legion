/**
 * AgentPanelDock 的焦点管理（issue #795 PR①，沿用 #790 overlayChrome 的
 * 契约）：打开/展开时焦点进面板 surface，折叠时焦点移到右下角展开小条
 * （折叠会把含焦点的内容区切为 display:none，不移交则键盘用户丢失上下文），
 * 卸载时还原触发元素。preventScroll 防焦点驱动的页面跳动。
 * ref 用 callback ref + state 而非 useRef：react-rnd 挂载期 componentDidMount
 * 内 setState/forceUpdate 触发嵌套重渲染，首帧 passive effect 里 useRef 的
 * current 可能仍是 null（实测），callback ref 的 node 到位通知才可靠。
 */
import { useCallback, useEffect, useState, type RefCallback } from 'react'

export interface DockFocus {
  surfaceRef: RefCallback<HTMLDivElement>
  chipRef: RefCallback<HTMLButtonElement>
}

export function useDockFocus(collapsed: boolean): DockFocus {
  const [surfaceNode, setSurfaceNode] = useState<HTMLDivElement | null>(null)
  const [chipNode, setChipNode] = useState<HTMLButtonElement | null>(null)
  const surfaceRef = useCallback(
    (node: HTMLDivElement | null) => setSurfaceNode(node),
    []
  )
  const chipRef = useCallback(
    (node: HTMLButtonElement | null) => setChipNode(node),
    []
  )

  useEffect(() => {
    const previous = document.activeElement
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus({ preventScroll: true })
      }
    }
  }, [])

  useEffect(() => {
    const target = collapsed ? chipNode : surfaceNode
    target?.focus({ preventScroll: true })
  }, [collapsed, surfaceNode, chipNode])

  return { surfaceRef, chipRef }
}
