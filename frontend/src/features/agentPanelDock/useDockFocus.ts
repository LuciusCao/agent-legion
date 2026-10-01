/**
 * AgentPanelDock 的焦点管理（issue #795 PR①，沿用 #790 overlayChrome 的
 * 契约）：可见时焦点进面板 surface；hidden（#797 codex P2）显式归还到
 * useDockFocusRestore 的目标链（restoreFocusRef 显式目标 → 面板外最后聚焦
 * 元素 → 调用方指定选择器 → 挂载前元素）——hidden 后焦点若留在
 * display:none 子树或裸丢 body，键盘用户丢失上下文。折叠态已随 #795 收尾
 * 移除（不再需要 chip 焦点移交）。preventScroll 防焦点驱动的页面跳动。
 * ref 用 callback ref + state 而非 useRef：react-rnd 挂载期 componentDidMount
 * 内 setState/forceUpdate 触发嵌套重渲染，首帧 passive effect 里 useRef 的
 * current 可能仍是 null（实测），callback ref 的 node 到位通知才可靠。
 */
import { useEffect, useRef, useState } from 'react'
import type { RefCallback } from 'react'
import { useDockFocusRestore } from './useDockFocusRestore'
import { useNodeRef } from './useNodeRef'

export interface DockFocus {
  surfaceRef: RefCallback<HTMLDivElement>
}

export function useDockFocus(
  hidden = false,
  restoreFocusSelector?: string,
  restoreFocusRef?: { readonly current: HTMLElement | null }
): DockFocus {
  const [surfaceNode, setSurfaceNode] = useState<HTMLDivElement | null>(null)
  const surfaceRef = useNodeRef(setSurfaceNode)
  const { restoreFocus } = useDockFocusRestore(
    restoreFocusSelector,
    surfaceNode,
    restoreFocusRef
  )

  // 曾经可见标记：只有「可见→隐藏」转换才归还焦点；首次以隐藏态挂载
  // （窄屏首进的常驻隐藏 Dock）跳过——用户从未打开过面板，无焦点可还。
  const wasVisibleRef = useRef(false)

  useEffect(() => {
    if (hidden) {
      if (wasVisibleRef.current) restoreFocus()
      return
    }
    wasVisibleRef.current = true
    surfaceNode?.focus({ preventScroll: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restoreFocus 读 ref/现查 DOM，不依赖其函数身份
  }, [hidden, surfaceNode])

  return { surfaceRef }
}
