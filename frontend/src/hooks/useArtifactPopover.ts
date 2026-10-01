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
    // capture 阶段消费 Esc（#797 复审批次 P2）：popover 是当前顶层浮层，
    // Esc 只关它。bubble 阶段的 preventDefault 拦不住注册更早的 document
    // 级监听（AgentPanelDock 的 Esc 折叠先跑，同一击键双重消费）；capture
    // 在 document 上最先触发，stopPropagation + preventDefault 双保险。
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      onCloseRef.current()
    }
    const click = (e: MouseEvent) =>
      el && !el.contains(e.target as Node) && onCloseRef.current()
    document.addEventListener('keydown', key, true)
    document.addEventListener('mousedown', click)
    return () => {
      document.removeEventListener('keydown', key, true)
      document.removeEventListener('mousedown', click)
    }
  }, [])
  return ref
}
