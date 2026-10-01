/**
 * Esc 关闭抽屉（persistent variant 不走 Modal，Esc 语义自行承接）：
 * capture 阶段监听 + preventDefault——Dock 的 Esc 处理器
 * （useDockEscape）见到 defaultPrevented 跳过，抽屉是更上层先消费。
 * IME 组字中的 Esc 是取消候选（不关闭）。
 */
import { useEffect } from 'react'

export function useDrawerEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing || event.defaultPrevented)
        return
      event.preventDefault()
      onClose()
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, onClose])
}
