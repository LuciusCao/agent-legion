/**
 * 画布工具栏的让位偏移（#799 codex 复核 P2）：岛内容换行/变高时，画布内
 * absolute 定位的工具栏若写死 top 会被盖住——按双岛实测底边（相对画布
 * 顶部）让位。监听 window resize + 岛元素 ResizeObserver；首帧/无岛回落
 * 64px 安全距离（岛带高约 52px）。jsdom 无布局，回落安全距离。
 */
import { useEffect, useState } from 'react'
import type { RefObject } from 'react'

const ISLAND_SELECTORS = [
  '[data-testid="studio-identity-island"]',
  '[data-testid="studio-action-island"]',
]
const FALLBACK_OFFSET = 64

export function useCanvasIslandOffset(
  canvasRef: RefObject<HTMLElement | null>
): number {
  const [offset, setOffset] = useState(FALLBACK_OFFSET)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const update = () => {
      const canvasTop = canvas.getBoundingClientRect().top
      let bottom = 0
      for (const selector of ISLAND_SELECTORS) {
        const island = document.querySelector(selector)
        if (island) {
          bottom = Math.max(
            bottom,
            island.getBoundingClientRect().bottom - canvasTop
          )
        }
      }
      setOffset(bottom > 0 ? Math.ceil(bottom) + 8 : FALLBACK_OFFSET)
    }
    update()
    window.addEventListener('resize', update)
    const observers: ResizeObserver[] = []
    if (typeof ResizeObserver !== 'undefined') {
      for (const selector of ISLAND_SELECTORS) {
        const island = document.querySelector(selector)
        if (island) {
          const observer = new ResizeObserver(update)
          observer.observe(island)
          observers.push(observer)
        }
      }
    }
    return () => {
      window.removeEventListener('resize', update)
      for (const observer of observers) observer.disconnect()
    }
  }, [canvasRef])
  return offset
}
