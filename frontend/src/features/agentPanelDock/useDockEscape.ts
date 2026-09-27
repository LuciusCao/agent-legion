/**
 * Esc 折叠的 document 级监听（codex P2 on #796/#797）：非模态面板失焦
 * （用户点回底层页面）后 Esc 仍应折叠——挂在 document 而非 Paper。折叠/
 * hidden 态不挂；不抢已消费的 Esc：defaultPrevented 跳过，有全局
 * Modal/Menu（MUI ModalManager 体系，如 TokenUsage/菜单）开着时让给对方。
 * 回调经 ref 读最新值（codex P2 复审轮）：effect 只按开关状态挂/卸，
 * 若闭包冻结首渲染的 onEscape，拖拽/缩放后的 Esc 会把旧几何写回存储——
 * ref 保证每次击键读的是当帧回调。
 */
import { useEffect, useRef } from 'react'

export function useDockEscape(suppressed: boolean, onEscape: () => void): void {
  const onEscapeRef = useRef(onEscape)
  useEffect(() => {
    onEscapeRef.current = onEscape
  })
  useEffect(() => {
    if (suppressed) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (document.querySelector('.MuiModal-root')) return
      onEscapeRef.current()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [suppressed])
}
