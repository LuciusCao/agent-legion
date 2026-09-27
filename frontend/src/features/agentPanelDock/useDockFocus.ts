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

/**
 * Esc 折叠的 document 级监听（codex P2 on #796）：非模态面板失焦（用户点
 * 回底层页面）后 Esc 仍应折叠——挂在 document 而非 Paper。折叠态不挂；
 * 不抢已消费的 Esc：defaultPrevented 跳过，有全局 Modal/Menu（MUI
 * ModalManager 体系，如 TokenUsage/菜单）开着时让给对方。
 */
export function useDockEscape(collapsed: boolean, onEscape: () => void): void {
  useEffect(() => {
    if (collapsed) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (document.querySelector('.MuiModal-root')) return
      onEscape()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onEscape 每轮渲染重建但语义稳定；只在 collapsed 翻转时重挂（collapsed 是唯一行为输入）
  }, [collapsed])
}
