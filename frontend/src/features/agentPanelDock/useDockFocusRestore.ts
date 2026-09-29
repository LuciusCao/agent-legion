/**
 * AgentPanelDock 的焦点归还目标链（#797 codex 复审轮，从 useDockFocus 拆出
 * 保体积预算）：归还目标 = 面板外最后聚焦的可见元素（Dock 可见期间 focusin
 * 持续追踪，面板内/小条上的焦点不记——隐藏/折叠时焦点本就该离开面板，记了
 * 反而还回不可见子树）→ 调用方指定选择器（顶栏开关/头部入口按钮——首次
 * 关闭、无面板外 focusin 时的稳定目标）→ 挂载前元素（兜底）。
 * 挂载时记录当前焦点、卸载时归还（见 useEffect）；hidden 转换的归还在
 * useDockFocus 里调 restoreFocus()。
 */
import { useEffect, useRef } from 'react'

/**
 * 可见且可聚焦判定（#797 codex 轮 8/9 P2）：归还目标可能在 Dock 打开期间
 * 变为不可聚焦——响应式 CSS 隐藏（窄屏页签断点切换后 display:none）或禁用
 * （如发布按钮变 disabled）；isConnected 不够，focus() 静默失败、焦点落
 * body。checkVisibility 覆盖 display:none 祖先链与 visibility；jsdom 未
 * 实现它，退回 getComputedStyle 逐级检查（内联样式与样式表规则都判得到）。
 * 禁用/惰性经 disabled 属性、aria-disabled 与 inert 祖先排除。
 */
function isVisibleFocusable(el: HTMLElement): boolean {
  if (!el.isConnected) return false
  if ((el as { disabled?: boolean }).disabled === true) return false
  if (el.getAttribute('aria-disabled') === 'true') return false
  if (el.closest('[inert]')) return false
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

/** 焦点归还目标链的持有与追踪（hidden 期间也照记：还回目标被聚焦同样是
 * 「面板外最后聚焦」）。restoreFocusRef（#800 codex P2）是调用方在唤起
 * 瞬间显式记录的触发元素，链上最优先：key 重挂换目标时旧实例的卸载清理
 * 会先改写 activeElement，显式 ref 不吃这套交错。 */
export function useDockFocusRestore(
  restoreFocusSelector: string | undefined,
  surfaceNode: HTMLElement | null,
  chipNode: HTMLElement | null,
  restoreFocusRef?: { readonly current: HTMLElement | null }
): { restoreFocus: () => void } {
  const mountPreviousRef = useRef<Element | null>(null)
  const outsideRef = useRef<Element | null>(null)

  // 归还目标链：调用时现读 ref/现查 DOM，闭包不过期。逐级校验可见且未
  // 禁用，并做 focus 后验证（#797 codex 轮 9 P2 双保险）：activeElement 真
  // 变成目标才算成功——focus() 对禁用/惰性元素静默失败，只查属性有漏网
  // 场景，没变就继续走下一级兜底。
  const restoreFocus = (): void => {
    const candidates: (Element | null)[] = [
      restoreFocusRef?.current ?? null,
      outsideRef.current,
      restoreFocusSelector
        ? document.querySelector(restoreFocusSelector)
        : null,
    ]
    for (const candidate of candidates) {
      if (!(candidate instanceof HTMLElement) || !isVisibleFocusable(candidate))
        continue
      candidate.focus({ preventScroll: true })
      if (document.activeElement === candidate) return
    }
    const fallback = mountPreviousRef.current
    if (fallback instanceof HTMLElement && fallback.isConnected)
      fallback.focus({ preventScroll: true })
  }

  useEffect(() => {
    mountPreviousRef.current = document.activeElement
    return () => restoreFocus()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restoreFocus 读 ref/现查 DOM，挂载一次即可
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

  return { restoreFocus }
}
