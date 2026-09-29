/**
 * AgentPanelDock 的焦点归还目标链（#797 codex 复审轮，从 useDockFocus 拆出
 * 保体积预算）：归还目标 = 面板外最后聚焦的可见元素（Dock 可见期间 focusin
 * 持续追踪，面板内/小条上的焦点不记——隐藏/折叠时焦点本就该离开面板，记了
 * 反而还回不可见子树）→ 调用方指定选择器（顶栏开关/头部入口按钮——首次
 * 关闭、无面板外 focusin 时的稳定目标）→ 挂载前元素（兜底）。
 * 挂载时记录当前焦点、卸载时归还（见 useEffect）；hidden 转换的归还在
 * useDockFocus 里调 restoreTarget()。
 */
import { useEffect, useRef } from 'react'

/** 焦点归还目标链的持有与追踪（hidden 期间也照记：还回目标被聚焦同样是
 * 「面板外最后聚焦」）。 */
/**
 * 可见且可聚焦判定（#797 复审批次 codex 轮 8 P2）：归还目标可能被响应式
 * CSS 隐藏（如窄屏 Agent 页签在断点切换后 display:none）——isConnected
 * 不够，focus 静默无效、焦点落 body。checkVisibility 覆盖 display:none
 * 祖先链与 visibility；jsdom 未实现它，退回 getComputedStyle 逐级检查
 * （内联样式与样式表规则都判得到）。
 */
function isVisibleFocusable(el: HTMLElement): boolean {
  if (!el.isConnected) return false
  if (typeof el.checkVisibility === 'function') {
    return el.checkVisibility({ checkVisibilityCSS: true })
  }
  let node: HTMLElement | null = el
  while (node) {
    const style = getComputedStyle(node)
    if (style.display === 'none' || style.visibility === 'hidden') return false
    node = node.parentElement
  }
  return true
}

export function useDockFocusRestore(
  restoreFocusSelector: string | undefined,
  surfaceNode: HTMLElement | null,
  chipNode: HTMLElement | null
): { restoreTarget: () => Element | null } {
  const mountPreviousRef = useRef<Element | null>(null)
  const outsideRef = useRef<Element | null>(null)

  // 归还目标链：调用时现读 ref/现查 DOM，闭包不过期。每一级都要求可见
  // 可聚焦，不可见（断点切换后 display:none）就继续走下一级兜底。
  const restoreTarget = (): Element | null => {
    const outside = outsideRef.current
    if (outside instanceof HTMLElement && isVisibleFocusable(outside))
      return outside
    if (restoreFocusSelector) {
      const designated = document.querySelector(restoreFocusSelector)
      if (designated instanceof HTMLElement && isVisibleFocusable(designated))
        return designated
    }
    return mountPreviousRef.current
  }

  useEffect(() => {
    mountPreviousRef.current = document.activeElement
    return () => {
      const target = restoreTarget()
      focusIfConnected(target)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restoreTarget 读 ref/现查 DOM，挂载一次即可
  }, [])

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
    // hidden 不挡追踪（注释见文件头）。
  }, [surfaceNode, chipNode])

  return { restoreTarget }
}

export function focusIfConnected(el: Element | null) {
  if (el instanceof HTMLElement && isVisibleFocusable(el))
    el.focus({ preventScroll: true })
}
