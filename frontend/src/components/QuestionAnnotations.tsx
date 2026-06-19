import { useEffect, useMemo, useRef, useState } from 'react'
import { renderLatexInHtml } from '../lib/latex'
import type { KeyInfoItem } from '../types'
import styles from './QuestionAnnotations.module.css'

export interface QuestionAnnotationsProps {
  wrapperRef: React.RefObject<HTMLDivElement | null>
  hiddenItems: KeyInfoItem[]
}

interface Measurement {
  id: string
  height: number
  targetTop: number
  targetRight: number
  targetHeight: number
}

interface FinalLayout {
  item: KeyInfoItem
  top: number
  height: number
  targetTop: number
  targetRight: number
  targetHeight: number
}

const GAP = 12

function escapeHtml(s: string): string {
  const map: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
  }
  return s.replace(/[&<>"]/g, (c) => map[c])
}

export function QuestionAnnotations({
  wrapperRef,
  hiddenItems,
}: QuestionAnnotationsProps) {
  const layerRef = useRef<HTMLDivElement>(null)
  const [measurements, setMeasurements] = useState<Measurement[]>([])
  const [wrapperHeight, setWrapperHeight] = useState(0)
  const [recalcTick, setRecalcTick] = useState(0)
  const [mathJaxTick, setMathJaxTick] = useState(0)
  const [measureRetryTick, setMeasureRetryTick] = useState(0)
  const lastTypesetKeyRef = useRef('')
  const mountedRef = useRef(true)
  const measureRetryCountRef = useRef(0)
  const measureRafRef = useRef<number | null>(null)

  const MAX_MEASURE_RETRIES = 5

  const hiddenKey = hiddenItems.map((i) => i.key_info_id).join(',')
  const measureKey = `${hiddenKey}:${recalcTick}:${mathJaxTick}:${measureRetryTick}`

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Reset cached measurements whenever the items, window size, or typeset output
  // changes. Clearing state via requestAnimationFrame keeps the update out of
  // the effect body while still invalidating DOM-derived measurements.
  useEffect(() => {
    lastTypesetKeyRef.current = ''
    measureRetryCountRef.current = 0
    if (measureRafRef.current !== null) {
      cancelAnimationFrame(measureRafRef.current)
      measureRafRef.current = null
    }
    requestAnimationFrame(() => {
      if (mountedRef.current) {
        setMeasurements([])
      }
    })
  }, [measureKey])

  useEffect(() => {
    function handleResize() {
      setRecalcTick((t) => t + 1)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  // Measure rendered card heights and highlight-span positions relative to the
  // annotation layer. DOM measurements can only happen after paint, so we update
  // React state from within an effect guarded by the measureKey.
  useEffect(() => {
    if (measureRafRef.current !== null) {
      cancelAnimationFrame(measureRafRef.current)
      measureRafRef.current = null
    }

    const cleanup = () => {
      if (measureRafRef.current !== null) {
        cancelAnimationFrame(measureRafRef.current)
        measureRafRef.current = null
      }
    }

    const wrapper = wrapperRef.current
    const layer = layerRef.current
    if (!wrapper || !layer || hiddenItems.length === 0) {
      return cleanup
    }

    // Already measured for the current measureKey.
    if (measurements.length === hiddenItems.length) {
      return cleanup
    }

    const layerRect = layer.getBoundingClientRect()
    const spans = Array.from(wrapper.querySelectorAll('[data-ids]'))
    const next: Measurement[] = []

    for (const item of hiddenItems) {
      const targetSpan = spans.find((span) => {
        const raw = span.getAttribute('data-ids') || ''
        const ids = raw
          .split(',')
          .map((id) => id.trim())
          .filter(Boolean)
        return ids.includes(item.key_info_id)
      })
      const card = document.getElementById(`annotation-${item.key_info_id}`)
      if (!targetSpan || !card) continue

      const spanRect = targetSpan.getBoundingClientRect()
      const cardRect = card.getBoundingClientRect()
      next.push({
        id: item.key_info_id,
        height: cardRect.height,
        targetTop: spanRect.top - layerRect.top,
        targetRight: spanRect.right - layerRect.left,
        targetHeight: spanRect.height,
      })
    }

    if (next.length !== hiddenItems.length) {
      // Some cards or highlighted spans are not in the DOM yet. Retry on the
      // next animation frame, but cap retries to avoid an infinite loop.
      if (measureRetryCountRef.current < MAX_MEASURE_RETRIES) {
        measureRetryCountRef.current += 1
        measureRafRef.current = requestAnimationFrame(() => {
          measureRafRef.current = null
          setMeasureRetryTick((t) => t + 1)
        })
      }
      return cleanup
    }

    measureRetryCountRef.current = 0
    const wrapperRect = wrapper.getBoundingClientRect()
    // Updating state from an effect is normally discouraged; defer the update
    // to requestAnimationFrame so it does not happen synchronously inside the
    // effect body.
    measureRafRef.current = requestAnimationFrame(() => {
      measureRafRef.current = null
      if (mountedRef.current) {
        setMeasurements(next)
        setWrapperHeight(wrapperRect.height)
      }
    })

    return cleanup
  }, [
    hiddenItems,
    measurements.length,
    recalcTick,
    mathJaxTick,
    measureRetryTick,
    wrapperHeight,
    wrapperRef,
  ])

  const itemMap = useMemo(
    () => new Map(hiddenItems.map((item) => [item.key_info_id, item])),
    [hiddenItems]
  )

  const finalLayouts = useMemo<FinalLayout[]>(() => {
    if (measurements.length !== hiddenItems.length) {
      return []
    }

    const sorted = measurements
      .map((m) => ({
        ...m,
        center: m.targetTop + m.targetHeight / 2,
        item: itemMap.get(m.id)!,
      }))
      .sort((a, b) => a.center - b.center)

    const layouts: FinalLayout[] = []
    let prevBottom = -Infinity

    for (const m of sorted) {
      let top = m.center - m.height / 2
      if (top < prevBottom + GAP) {
        top = prevBottom + GAP
      }
      layouts.push({
        item: m.item,
        top,
        height: m.height,
        targetTop: m.targetTop,
        targetRight: m.targetRight,
        targetHeight: m.targetHeight,
      })
      prevBottom = top + m.height
    }

    return layouts
  }, [measurements, hiddenItems, itemMap])

  const paths = useMemo(() => {
    return finalLayouts.map((layout) => {
      const startX = layout.targetRight
      const startY = layout.targetTop + layout.targetHeight / 2
      const endX = 0
      const endY = layout.top + layout.height / 2
      const midX = startX / 2

      return (
        `M ${startX.toFixed(1)} ${startY.toFixed(1)} ` +
        `C ${midX.toFixed(1)} ${startY.toFixed(1)}, ` +
        `${midX.toFixed(1)} ${endY.toFixed(1)}, ` +
        `${endX.toFixed(1)} ${endY.toFixed(1)}`
      )
    })
  }, [finalLayouts])

  const layerHeight = useMemo(() => {
    if (finalLayouts.length === 0) {
      return wrapperHeight || undefined
    }
    const last = finalLayouts[finalLayouts.length - 1]
    const cardsBottom = last.top + last.height + GAP
    return Math.max(wrapperHeight, cardsBottom)
  }, [finalLayouts, wrapperHeight])

  // Typeset LaTeX in the cards, then remeasure because rendered math can change
  // card sizes.
  useEffect(() => {
    if (finalLayouts.length === 0) return

    const typesetKey = finalLayouts.map((l) => l.item.key_info_id).join(',')
    if (lastTypesetKeyRef.current === typesetKey) return
    lastTypesetKeyRef.current = typesetKey

    const layer = layerRef.current
    if (!layer || typeof window === 'undefined') return

    const mj = (
      window as unknown as {
        MathJax?: { typesetPromise?: (nodes: unknown[]) => Promise<void> }
      }
    ).MathJax

    if (mj?.typesetPromise) {
      mj.typesetPromise([layer])
        .catch(() => {
          // MathJax failures should not break the UI.
        })
        .then(() => {
          if (!mountedRef.current) return
          setMathJaxTick((t) => t + 1)
        })
    }
  }, [finalLayouts])

  if (hiddenItems.length === 0) return null

  const isReady = finalLayouts.length === hiddenItems.length

  return (
    <div
      ref={layerRef}
      className={styles.annotationLayer}
      style={{ height: layerHeight }}
    >
      <svg className={styles.connectionSvg}>
        {paths.map((d, idx) => (
          <path key={idx} className={styles.connectionLine} d={d} />
        ))}
      </svg>
      {hiddenItems.map((item) => {
        const layout = finalLayouts.find(
          (l) => l.item.key_info_id === item.key_info_id
        )
        return (
          <div
            key={item.key_info_id}
            id={`annotation-${item.key_info_id}`}
            className={styles.annotationCard}
            style={{
              top: layout?.top ?? 0,
              opacity: isReady ? 1 : 0,
            }}
            dangerouslySetInnerHTML={{
              __html: renderLatexInHtml(
                escapeHtml(item.content.derivation || '')
              ),
            }}
          />
        )
      })}
    </div>
  )
}
