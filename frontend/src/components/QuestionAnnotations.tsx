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
  const lastTypesetKeyRef = useRef('')

  const hiddenKey = hiddenItems.map((i) => i.key_info_id).join(',')
  const measureKey = `${hiddenKey}:${recalcTick}:${mathJaxTick}`

  // Reset cached measurements whenever the items, window size, or typeset output
  // changes. Updating state inside an effect is normally discouraged, but it is
  // the simplest way to invalidate DOM-derived measurements when the inputs change.
  useEffect(() => {
    lastTypesetKeyRef.current = ''
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMeasurements([])
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
    const wrapper = wrapperRef.current
    const layer = layerRef.current
    if (!wrapper || !layer || hiddenItems.length === 0) {
      return
    }

    // Already measured for the current measureKey.
    if (measurements.length === hiddenItems.length) {
      return
    }

    const layerRect = layer.getBoundingClientRect()
    const spans = Array.from(wrapper.querySelectorAll('.highlight'))
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
      // DOM is not fully ready yet; wait for the next effect cycle.
      return
    }

    const wrapperRect = wrapper.getBoundingClientRect()
    // Updating state from an effect is normally discouraged, but here it is the
    // only way to turn post-paint DOM measurements (card heights and highlight
    // positions) into rendered layout positions and SVG paths.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMeasurements(next)
    setWrapperHeight(wrapperRect.height)
  }, [
    hiddenItems,
    measurements.length,
    recalcTick,
    mathJaxTick,
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
