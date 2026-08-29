import { useCallback, useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

export const RIGHT_PANEL_MIN_WIDTH = 320
// 上限 1600：默认 1:1 在宽屏上本就超过旧的 720（2560 宽视口右半约 1200px），
// 拖拽不应在第一下就跳回一个比默认值还小的上限。
export const RIGHT_PANEL_MAX_WIDTH = 1600

const STORAGE_KEY = 'studio.rightPanel.width'
const CSS_VAR = '--studio-right-width'
const HANDLE_WIDTH = 6

export function clampRightPanelWidth(value: number): number {
  return Math.min(
    RIGHT_PANEL_MAX_WIDTH,
    Math.max(RIGHT_PANEL_MIN_WIDTH, Math.round(value))
  )
}

/** null = 未设置：CSS grid 回落 minmax(0, 1fr)，左右严格 1:1（随视口缩放）。 */
function loadStoredWidth(): number | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === null) return null
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? clampRightPanelWidth(parsed) : null
  } catch {
    return null
  }
}

/** 未设置态下起拖：实测 grid 容器宽度推算当前右栏像素宽，让拖动从视觉位置连续开始。 */
function measureCurrentWidth(target: EventTarget | null): number {
  const parent = target instanceof HTMLElement ? target.parentElement : null
  const containerWidth = parent?.getBoundingClientRect().width ?? 0
  if (!(containerWidth > 0)) return RIGHT_PANEL_MIN_WIDTH
  // 容器 = 左 1fr + 6px 分隔条 + 右 1fr
  return clampRightPanelWidth((containerWidth - HANDLE_WIDTH) / 2)
}

/** 右栏宽度：拖过才写 localStorage（px）并经 CSS 变量驱动 grid 轨道；未拖过保持
 * 1:1（不设 CSS 变量）；双击分隔条复位即清回未设置态。 */
export function useStudioRightPanelWidth() {
  const [width, setWidth] = useState<number | null>(loadStoredWidth)
  const widthRef = useRef(width)
  const dragCleanup = useRef<(() => void) | null>(null)

  useEffect(() => {
    widthRef.current = width
    const rootStyle = document.documentElement.style
    try {
      if (width === null) {
        rootStyle.removeProperty(CSS_VAR)
        window.localStorage.removeItem(STORAGE_KEY)
      } else {
        rootStyle.setProperty(CSS_VAR, `${width}px`)
        window.localStorage.setItem(STORAGE_KEY, String(width))
      }
    } catch {
      // localStorage 不可用（隐私模式等）：宽度仅本次会话生效
    }
  }, [width])

  // 卸载时摘掉可能残留的拖拽监听（拖拽中路由切走）。
  useEffect(() => () => dragCleanup.current?.(), [])

  const startDrag = useCallback((event: ReactPointerEvent) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth =
      widthRef.current ?? measureCurrentWidth(event.currentTarget)
    const onMove = (move: globalThis.PointerEvent) => {
      setWidth(clampRightPanelWidth(startWidth + (startX - move.clientX)))
    }
    const onUp = () => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      dragCleanup.current = null
    }
    dragCleanup.current?.()
    dragCleanup.current = onUp
    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
  }, [])

  /** 复位 = 清回未设置态（1:1），而不是某个固定像素值。 */
  const resetWidth = useCallback(() => setWidth(null), [])

  return { width, startDrag, resetWidth }
}
