import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import {
  isStepperCompact,
  STEPPER_MIN_SEGMENT_PX,
} from '../../lib/stepperLayout'

// 紧凑形态判定的宽度模型（#1335）：可辨识下限 = 每段 STEPPER_MIN_SEGMENT_PX
// 且段间距已收缩到 0（stepperTrackGap 的下界）。实测轨道宽度分摊到每段不足
// 该下限时，逐段渲染失去辨识度，切换紧凑形态；阈值因此随可展示空间伸缩，
// 不再是固定节点数。
export function isStepperCompactForWidth(
  segmentCount: number,
  trackWidthPx: number
): boolean {
  if (segmentCount <= 0 || trackWidthPx <= 0) return false
  return trackWidthPx / segmentCount < STEPPER_MIN_SEGMENT_PX
}

export interface StepperCompactMode {
  /** 挂到 stepper 容器节点上量轨道宽度（紧凑/逐段两形态共用同一容器）。 */
  containerRef: RefObject<HTMLDivElement>
  compact: boolean
  /** 实测轨道宽度；未测得（无 ResizeObserver 或首帧回调前）为 null。 */
  trackWidthPx: number | null
}

export function useStepperCompactMode(
  segmentCount: number,
  compactThreshold?: number
): StepperCompactMode {
  const containerRef = useRef<HTMLDivElement>(null)
  const [trackWidthPx, setTrackWidthPx] = useState<number | null>(null)

  useEffect(() => {
    const element = containerRef.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (typeof width === 'number' && width > 0) {
        // 同值不置新 state，避免宽度抖动时渲染循环。
        setTrackWidthPx((prev) => (prev === width ? prev : width))
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // compactThreshold prop 是显式覆盖通道（#1257 契约保留，按节点数判定）；
  // 缺省按实测宽度动态判定（#1335）；宽度未测得时退回节点数阈值，保证无
  // ResizeObserver 环境行为与 #1257 一致。
  const compact =
    compactThreshold !== undefined
      ? isStepperCompact(segmentCount, compactThreshold)
      : trackWidthPx !== null
        ? isStepperCompactForWidth(segmentCount, trackWidthPx)
        : isStepperCompact(segmentCount)

  return { containerRef, compact, trackWidthPx }
}
