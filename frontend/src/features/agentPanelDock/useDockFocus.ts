/**
 * AgentPanelDock 的焦点管理（issue #795 PR①，沿用 #790 overlayChrome 的
 * 契约）：打开/展开时焦点进面板 surface，折叠时焦点移到右下角展开小条
 * （折叠会把含焦点的内容区切为 display:none，不移交则键盘用户丢失上下文）。
 * hidden（#797 codex P2）与折叠分开处理：hidden 连小条都不渲染——若当折叠
 * 处理去聚焦 chipRef（不存在），焦点会留在 display:none 子树或裸丢 body；
 * 显式归还到 useDockFocusRestore 的目标链（面板外最后聚焦元素 → 调用方
 * 指定选择器 → 挂载前元素）。preventScroll 防焦点驱动的页面跳动。
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
  chipRef: RefCallback<HTMLButtonElement>
}

export function useDockFocus(
  collapsed: boolean,
  hidden = false,
  restoreFocusSelector?: string
): DockFocus {
  const [surfaceNode, setSurfaceNode] = useState<HTMLDivElement | null>(null)
  const [chipNode, setChipNode] = useState<HTMLButtonElement | null>(null)
  const surfaceRef = useNodeRef(setSurfaceNode)
  const chipRef = useNodeRef(setChipNode)
  const { restoreFocus } = useDockFocusRestore(
    restoreFocusSelector,
    surfaceNode,
    chipNode
  )

  // 曾经可见标记：只有「可见→隐藏」转换才归还焦点；首次以隐藏态挂载
  // （窄屏首进的常驻隐藏 Dock）跳过——用户从未打开过面板，无焦点可还。
  const wasVisibleRef = useRef(false)
  // 首挂即折叠（localStorage 记忆 collapsed=true）同样不抢焦点（#797
  // 复审批次 P3，与 hidden 首挂同思路）：用户从未在本会话打开面板，chip
  // 不该夺走既有焦点。用户点 chip 展开过（collapsed 翻过 false）后守卫
  // 解除，后续折叠/展开照常移交。
  const initialCollapsedRef = useRef(collapsed)

  useEffect(() => {
    if (hidden) {
      if (wasVisibleRef.current) restoreFocus()
      return
    }
    wasVisibleRef.current = true
    if (!collapsed) initialCollapsedRef.current = false
    if (collapsed && initialCollapsedRef.current) return
    const node = collapsed ? chipNode : surfaceNode
    node?.focus({ preventScroll: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restoreFocus 读 ref/现查 DOM，不依赖其函数身份
  }, [collapsed, hidden, surfaceNode, chipNode])

  return { surfaceRef, chipRef }
}
