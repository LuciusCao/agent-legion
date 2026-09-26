/**
 * 「定制预览」覆盖面板的 chrome 行为（从组件抽出保体积预算）：焦点管理 +
 * AppBar 实测底边。
 * - 焦点（codex P2-A id 4111642731）：折叠会把含焦点的内容区切为
 *   display:none，不移交焦点则键盘用户丢失上下文——折叠时焦点移到展开
 *   小条、展开/打开时回到 surface，卸载时还原触发点（preventScroll 防焦点
 *   驱动的页面跳动）。
 * - AppBar 实测底边（codex P2 id 4111642734 同轮）：复用
 *   useAppBarBottom（getBoundingClientRect().bottom，TokenUsageDialog 同款）
 *   ——版本芯片/放大字体时 AppBar 实测高度超过声明 min-height，固定值会盖
 *   住 AppBar 底部。测量值经 inline style 的 --overlay-top-inset 变量下发
 *   给 sx（customizePreviewOverlaySx）；首帧未测量（0）时不下发，sx 内的
 *   变量链回退 --app-bar-height 声明值。
 */
import { useEffect, useRef, type CSSProperties, type RefObject } from 'react'
import { useAppBarBottom } from '../../hooks/useAppBarBottom'

export interface OverlayChrome {
  surfaceRef: RefObject<HTMLDivElement>
  pillRef: RefObject<HTMLButtonElement>
  /** 下发 --overlay-top-inset 的 inline style（未测量时为空对象）。 */
  insetStyle: CSSProperties
}

export function useOverlayChrome(collapsed: boolean): OverlayChrome {
  const surfaceRef = useRef<HTMLDivElement>(null)
  const pillRef = useRef<HTMLButtonElement>(null)
  const appBarBottom = useAppBarBottom()

  useEffect(() => {
    const previous = document.activeElement
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus({ preventScroll: true })
      }
    }
  }, [])

  useEffect(() => {
    const target = collapsed ? pillRef.current : surfaceRef.current
    target?.focus({ preventScroll: true })
  }, [collapsed])

  return {
    surfaceRef,
    pillRef,
    insetStyle:
      appBarBottom > 0
        ? ({ '--overlay-top-inset': `${appBarBottom}px` } as CSSProperties)
        : {},
  }
}
