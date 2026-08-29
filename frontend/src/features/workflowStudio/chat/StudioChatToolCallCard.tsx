import { useState } from 'react'
import type { ToolCallView } from './studioChatMessages'
import styles from './StudioChatPanel.module.css'

const STATUS_ICON: Record<string, string> = {
  completed: '✓',
  failed: '✗',
  in_progress: '…',
  pending: '…',
}

function outputSummary(call: ToolCallView): string | null {
  if (!call.outputText) return null
  const firstLine = call.outputText.trim().split('\n')[0]
  return firstLine.length > 80 ? `${firstLine.slice(0, 80)}…` : firstLine
}

export function StudioChatToolCallCard({ call }: { call: ToolCallView }) {
  const [open, setOpen] = useState(false)
  const icon = STATUS_ICON[call.status] ?? '…'
  const summary = outputSummary(call)
  return (
    <div className={styles.toolCall} data-status={call.status || undefined}>
      <button
        type="button"
        className={styles.toolCallTrigger}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span
          className={
            call.status === 'failed' ? styles.toolCallFailed : styles.toolCallOk
          }
          aria-hidden="true"
        >
          {icon}
        </span>
        <code>{call.title || call.toolCallId}</code>
        {summary && <span className={styles.toolCallSummary}>{summary}</span>}
      </button>
      {open && (
        <div className={styles.toolCallDetail}>
          {call.rawInput && <pre>{JSON.stringify(call.rawInput, null, 2)}</pre>}
          {call.outputText && <pre>{call.outputText}</pre>}
        </div>
      )}
    </div>
  )
}
