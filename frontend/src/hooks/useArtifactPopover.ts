import { useEffect, useRef } from 'react'

export function useArtifactPopover(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    const el = ref.current
    el?.querySelector('button')?.focus()
    const key = (e: KeyboardEvent) =>
      e.key === 'Escape' && (e.stopPropagation(), onCloseRef.current())
    const click = (e: MouseEvent) =>
      el && !el.contains(e.target as Node) && onCloseRef.current()
    document.addEventListener('keydown', key)
    document.addEventListener('mousedown', click)
    return () => {
      document.removeEventListener('keydown', key)
      document.removeEventListener('mousedown', click)
    }
  }, [])
  return ref
}
