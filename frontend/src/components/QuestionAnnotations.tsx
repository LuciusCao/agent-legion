import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { renderLatexInHtml } from '../lib/latex'
import type { KeyInfoItem } from '../types'
import styles from './QuestionAnnotations.module.css'

export interface QuestionAnnotationsProps {
  wrapperRef: React.RefObject<HTMLDivElement | null>
  hiddenItems: KeyInfoItem[]
}

interface AnnotationLayout {
  item: KeyInfoItem
  top: number
  targetRect: DOMRect
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
  const [layouts, setLayouts] = useState<AnnotationLayout[]>([])
  const [paths, setPaths] = useState<string[]>([])
  const measuredRef = useRef(false)
  const lastTypesetKeyRef = useRef('')
  const [recalcTick, setRecalcTick] = useState(0)
  const [mathJaxTick, setMathJaxTick] = useState(0)

  const hiddenKey = hiddenItems.map((i) => i.key_info_id).join(',')

  useEffect(() => {
    function handleResize() {
      setRecalcTick((t) => t + 1)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  // First pass: locate highlighted spans and compute initial card positions.
  useLayoutEffect(() => {
    const wrapper = wrapperRef.current
    const layer = layerRef.current
    if (!wrapper || !layer || hiddenItems.length === 0) {
      setLayouts([])
      setPaths([])
      measuredRef.current = false
      return
    }

    const wrapperRect = wrapper.getBoundingClientRect()
    const spans = Array.from(wrapper.querySelectorAll('.highlight'))
    const next: AnnotationLayout[] = []

    for (const item of hiddenItems) {
      const targetSpan = spans.find((span) => {
        const raw = span.getAttribute('data-ids') || ''
        const ids = raw
          .split(',')
          .map((id) => id.trim())
          .filter(Boolean)
        return ids.includes(item.key_info_id)
      })
      if (!targetSpan) continue

      const rect = targetSpan.getBoundingClientRect()
      next.push({
        item,
        top: rect.top - wrapperRect.top,
        targetRect: rect,
      })
    }

    next.sort((a, b) => a.targetRect.top - b.targetRect.top)
    setLayouts(next)
    measuredRef.current = false
  }, [wrapperRef, hiddenKey, recalcTick, mathJaxTick])

  // Second pass: measure rendered card heights, resolve overlaps, and draw curves.
  useLayoutEffect(() => {
    const wrapper = wrapperRef.current
    const layer = layerRef.current
    if (!wrapper || !layer || layouts.length === 0) {
      setPaths([])
      return
    }
    if (measuredRef.current) return

    const wrapperRect = wrapper.getBoundingClientRect()
    const layerRect = layer.getBoundingClientRect()

    const elements = layouts
      .map((l) => document.getElementById(`annotation-${l.item.key_info_id}`))
      .filter((el): el is HTMLElement => el !== null)
    const heights = elements.map((el) => el.getBoundingClientRect().height)

    const adjusted: AnnotationLayout[] = []
    let prevBottom = -Infinity
    let maxBottom = 0

    for (let i = 0; i < layouts.length; i++) {
      const layout = layouts[i]
      const height = heights[i] ?? 0
      let top = layout.top
      if (top < prevBottom + GAP) {
        top = prevBottom + GAP
      }
      adjusted.push({ ...layout, top })
      prevBottom = top + height
      maxBottom = Math.max(maxBottom, prevBottom)
    }

    const changed = adjusted.some((l, i) => l.top !== layouts[i].top)
    if (changed) {
      setLayouts(adjusted)
    }
    measuredRef.current = true

    // Make the layer tall enough for both the stem and the cards.
    layer.style.height = `${Math.max(wrapperRect.height, maxBottom + GAP)}px`

    const nextPaths: string[] = []
    for (let i = 0; i < adjusted.length; i++) {
      const layout = adjusted[i]
      const height = heights[i] ?? 0
      const startX = layout.targetRect.right - wrapperRect.left
      const startY =
        layout.targetRect.top - wrapperRect.top + layout.targetRect.height / 2
      const endX = layerRect.left - wrapperRect.left
      const endY = layout.top + height / 2
      const midX = (startX + endX) / 2

      nextPaths.push(
        `M ${startX.toFixed(1)} ${startY.toFixed(1)} ` +
          `C ${midX.toFixed(1)} ${startY.toFixed(1)}, ` +
          `${midX.toFixed(1)} ${endY.toFixed(1)}, ` +
          `${endX.toFixed(1)} ${endY.toFixed(1)}`
      )
    }
    setPaths(nextPaths)
  }, [layouts, wrapperRef])

  // Typeset LaTeX in the cards, then remeasure because rendered math changes size.
  useEffect(() => {
    if (layouts.length === 0) return

    const typesetKey = layouts.map((l) => l.item.key_info_id).join(',')
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
          measuredRef.current = false
          setMathJaxTick((t) => t + 1)
        })
    }
  }, [layouts])

  if (hiddenItems.length === 0) return null

  return (
    <div ref={layerRef} className={styles.annotationLayer}>
      <svg className={styles.connectionSvg}>
        {paths.map((d, idx) => (
          <path key={idx} className={styles.connectionLine} d={d} />
        ))}
      </svg>
      {layouts.map((layout) => (
        <div
          key={layout.item.key_info_id}
          id={`annotation-${layout.item.key_info_id}`}
          className={styles.annotationCard}
          style={{ top: layout.top }}
          dangerouslySetInnerHTML={{
            __html: renderLatexInHtml(
              escapeHtml(layout.item.content.derivation || '')
            ),
          }}
        />
      ))}
    </div>
  )
}
