/**
 * Studio 右侧两个抽屉（节点详情/共享素材）paper 的内联样式——同一份几何，
 * 不在两个抽屉里各自拼：
 * - 栈位 z-index + Esc 关闭（useDrawerEscape，#812 D2：视觉序 == Esc 栈序）；
 * - 窄屏顶边让位变量（#817 方向 a）：页签行实测视口底边写进
 *   STUDIO_DRAWER_TOP_INSET_VAR，studioDrawerFloat.module.css 只在 ≤900px
 *   断点消费——抽屉顶边让到页签行之下，「画布 / Agent」两个页签在抽屉打开
 *   时都可点；宽屏页签行 display:none 实测 0 且断点外不消费，几何不变；
 * - hidden（#812 P2-2：窄屏非画布页签）display:none 不卸载，同时出 Esc 栈。
 */
import type { CSSProperties } from 'react'
import { STUDIO_DRAWER_TOP_INSET_VAR } from './studioDrawerGeometry'
import { useDrawerEscape } from './useDrawerEscape'
import { type NavMetric, useMeasuredNav } from './useStudioMobileNavHeight'

const NAV_ROW_SELECTOR = '[data-testid="studio-mobile-nav-row"]'

/** 直接量页签行底边而不是 AppBar + nav 高度相加——页签行上方的任何 chrome
 * 都自动计入；同时观察 AppBar（版本芯片/放大字体把它撑高时页签行整体下移，
 * 但页签行自身尺寸不变，只观察它收不到通知）。 */
const NAV_ROW_BOTTOM: NavMetric = {
  target: NAV_ROW_SELECTOR,
  observed: [NAV_ROW_SELECTOR, '[data-testid="app-bar"]'],
  read: (rect) => rect.bottom,
}

/** 移动端页签行的实测视口底边（无页签行 / 宽屏 display:none → 0）。 */
export function useStudioMobileNavBottom(): number {
  return useMeasuredNav(NAV_ROW_BOTTOM)
}

export function studioDrawerPaperStyle({
  zIndex,
  topInset,
  hidden,
}: {
  zIndex: number
  topInset: number
  hidden: boolean
}): CSSProperties {
  return {
    zIndex,
    [STUDIO_DRAWER_TOP_INSET_VAR]: `${topInset}px`,
    ...(hidden ? { display: 'none' } : {}),
  } as CSSProperties
}

export function useStudioDrawerPaperStyle(
  open: boolean,
  onClose: () => void,
  hidden: boolean
): CSSProperties {
  const zIndex = useDrawerEscape(open, onClose, hidden)
  const topInset = useStudioMobileNavBottom()
  return studioDrawerPaperStyle({ zIndex, topInset, hidden })
}
