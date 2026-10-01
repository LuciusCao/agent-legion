import {
  Fragment,
  useEffect,
  useState,
  type ReactNode,
  type RefObject,
} from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'

/** Keep expensive markdown/tool bodies mounted only near the viewport. */
export function StudioChatWindow({
  ids,
  scrollRef,
  pinnedRef,
  renderRow,
}: {
  ids: string[]
  scrollRef: RefObject<HTMLDivElement>
  pinnedRef: RefObject<boolean>
  renderRow: (index: number) => ReactNode
}) {
  const enabled = ids.length > 80
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(
    null
  )
  useEffect(() => {
    // The parent attaches its ref after this child's first layout effect.
    setScrollElement(scrollRef.current)
  }, [scrollRef])
  // eslint-disable-next-line react-hooks/incompatible-library -- Same measured virtualizer as the job list.
  const virtualizer = useVirtualizer({
    count: ids.length,
    enabled,
    getScrollElement: () => scrollElement,
    getItemKey: (index) => ids[index],
    estimateSize: () => 100,
    overscan: 8,
    gap: 10,
  })
  const height = virtualizer.getTotalSize()
  useEffect(() => {
    const element = scrollRef.current
    if (enabled && element && pinnedRef.current)
      element.scrollTop = element.scrollHeight
  }, [enabled, height, scrollRef, pinnedRef])
  if (!enabled)
    return ids.map((id, index) => (
      <Fragment key={id}>{renderRow(index)}</Fragment>
    ))
  return (
    <div style={{ height, position: 'relative', flexShrink: 0, width: '100%' }}>
      {virtualizer.getVirtualItems().map((row) => (
        <div
          key={row.key}
          data-index={row.index}
          ref={virtualizer.measureElement}
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            transform: `translateY(${row.start}px)`,
          }}
        >
          <Row>{renderRow(row.index)}</Row>
        </div>
      ))}
    </div>
  )
}

function Row({ children }: { children: ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {children}
    </div>
  )
}
