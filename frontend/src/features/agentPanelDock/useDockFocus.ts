/**
 * AgentPanelDock 的焦点管理（issue #795 PR①，沿用 #790 overlayChrome 的
 * 契约）：打开/展开时焦点进面板 surface，折叠时焦点移到右下角展开小条
 * （折叠会把含焦点的内容区切为 display:none，不移交则键盘用户丢失上下文），
 * 卸载时还原触发元素。preventScroll 防焦点驱动的页面跳动。
 * hidden（#797 codex P2）与折叠分开处理：hidden 连小条都不渲染——若当折叠
 * 处理去聚焦 chipRef（不存在），焦点会留在 display:none 子树或裸丢 body；
 * 显式把焦点还给挂载前的触发控件（顶栏开关/页签），恢复显示时回 surface。
 * ref 用 callback ref + state 而非 useRef：react-rnd 挂载期 componentDidMount
 * 内 setState/forceUpdate 触发嵌套重渲染，首帧 passive effect 里 useRef 的
 * current 可能仍是 null（实测），callback ref 的 node 到位通知才可靠。
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefCallback,
} from 'react'

export interface DockFocus {
  surfaceRef: RefCallback<HTMLDivElement>
  chipRef: RefCallback<HTMLButtonElement>
}

export function useDockFocus(collapsed: boolean, hidden = false): DockFocus {
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
  // 挂载前的焦点元素（触发控件）：卸载或 hidden 时还原。
  const previousRef = useRef<Element | null>(null)

  useEffect(() => {
    previousRef.current = document.activeElement
    return () => {
      const previous = previousRef.current
      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus({ preventScroll: true })
      }
    }
  }, [])

  useEffect(() => {
    if (hidden) {
      // hidden：surface/chip 均不可见——焦点还给触发控件，不丢进不可见子树。
      const previous = previousRef.current
      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus({ preventScroll: true })
      }
      return
    }
    const target = collapsed ? chipNode : surfaceNode
    target?.focus({ preventScroll: true })
  }, [collapsed, hidden, surfaceNode, chipNode])

  return { surfaceRef, chipRef }
}
