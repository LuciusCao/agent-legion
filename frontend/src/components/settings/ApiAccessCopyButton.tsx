import { useEffect, useState } from 'react'
import styles from './ApiAccess.module.css'

type CopyState = 'idle' | 'copied' | 'failed'

/**
 * 「外部对接」section 的复制按钮（#870）：复制成功 / 失败就地反馈，
 * 2 秒后复位。剪贴板不可用（非安全上下文等）时提示手动选择。
 */
export function ApiAccessCopyButton({
  text,
  label = '复制',
  ariaLabel,
}: {
  text: string
  label?: string
  ariaLabel?: string
}) {
  const [state, setState] = useState<CopyState>('idle')

  useEffect(() => {
    if (state === 'idle') return
    const timer = window.setTimeout(() => setState('idle'), 2000)
    return () => window.clearTimeout(timer)
  }, [state])

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
  }

  return (
    <button
      type="button"
      className={styles.secondaryButton}
      aria-label={ariaLabel}
      aria-live="polite"
      onClick={() => void handleCopy()}
    >
      {state === 'copied'
        ? '已复制'
        : state === 'failed'
          ? '复制失败，请手动选择'
          : label}
    </button>
  )
}
