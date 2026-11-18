import { useRef, useState } from 'react'
import styles from './StudioChatView.module.css'

export function StudioChatHistoryLoad({
  sessionId,
  hasMoreHistory,
  onLoadOlderHistory,
}: {
  sessionId: string | null
  hasMoreHistory: boolean
  onLoadOlderHistory?: () => Promise<boolean>
}) {
  const [request, setRequest] = useState<{
    sessionId: string | null
    token: number
  } | null>(null)
  const tokenRef = useRef(0)
  const pending = request !== null && request.sessionId === sessionId

  async function load() {
    if (!onLoadOlderHistory || !hasMoreHistory || pending) return
    const token = ++tokenRef.current
    setRequest({ sessionId, token })
    try {
      await onLoadOlderHistory()
    } finally {
      setRequest((current) => (current?.token === token ? null : current))
    }
  }

  if (!hasMoreHistory || !onLoadOlderHistory) return null
  return (
    <div className={styles.historyAction}>
      <button
        type="button"
        className={styles.historyButton}
        disabled={pending}
        aria-busy={pending}
        onClick={() => void load()}
      >
        {pending ? '正在加载…' : '加载更早历史'}
      </button>
    </div>
  )
}
