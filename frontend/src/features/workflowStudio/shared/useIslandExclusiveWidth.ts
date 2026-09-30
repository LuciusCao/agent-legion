/**
 * 双岛互斥宽度（#804 codex 轮 2 P1，从 StudioCanvasIslands 拆出保体积
 * 预算）：左岛 max-width = 画布列宽 - 右岛实测宽 - 间距（12*3），resize +
 * 双岛 ResizeObserver 驱动；详情列打开时画布列变窄自动收紧。jsdom 无布局
 * （clientWidth/rect 恒 0）→ 不封顶，回落 CSS 的 max-width。
 */
import { useEffect, useRef, useState } from 'react'

export function useIslandExclusiveWidth() {
  const identityRef = useRef<HTMLDivElement>(null)
  const actionRef = useRef<HTMLDivElement>(null)
  const [identityMaxWidth, setIdentityMaxWidth] = useState<number | null>(null)
  useEffect(() => {
    const identity = identityRef.current
    const action = actionRef.current
    if (!identity || !action) return
    const update = () => {
      const parent = identity.offsetParent as HTMLElement | null
      const parentWidth = parent?.clientWidth ?? 0
      const actionWidth = action.getBoundingClientRect().width
      if (parentWidth > 0 && actionWidth > 0)
        setIdentityMaxWidth(parentWidth - actionWidth - 36)
    }
    update()
    window.addEventListener('resize', update)
    const observer =
      typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null
    if (observer) {
      observer.observe(identity)
      observer.observe(action)
    }
    return () => {
      window.removeEventListener('resize', update)
      observer?.disconnect()
    }
  }, [])
  return { identityRef, actionRef, identityMaxWidth }
}
