import { useEffect, useRef } from 'react'

export function useArtifactPopover(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    el?.querySelector('button')?.focus()
    const key = (e: KeyboardEvent) =>
      e.key === 'Escape' && (e.stopPropagation(), onClose())
    const click = (e: MouseEvent) =>
      el && !el.contains(e.target as Node) && onClose()
    document.addEventListener('keydown', key)
    document.addEventListener('mousedown', click)
    return () => {
      document.removeEventListener('keydown', key)
      document.removeEventListener('mousedown', click)
    }
  }, [onClose])
  return ref
}
