import { useMemo, useState } from 'react'
import type { ToolCallView } from './studioChatMessages'
import { StudioChatTruncatedPre } from './StudioChatTruncatedPre'
import styles from './StudioChatPanel.module.css'

const STATUS_ICON: Record<string, string> = {
  completed: '✓',
  failed: '✗',
  in_progress: '…',
  pending: '…',
}

function outputSummary(call: ToolCallView): string | null {
  if (!call.outputText) return null
  // A collapsed card only needs its preview, not an array of every output line.
  const firstLine = call.outputText.trim().slice(0, 81).split('\n', 1)[0]
  return firstLine.length > 80 ? `${firstLine.slice(0, 80)}…` : firstLine
}

export function StudioChatToolCallCard({ call }: { call: ToolCallView }) {
  const [open, setOpen] = useState(false)
  const icon = STATUS_ICON[call.status] ?? '…'
  const summary = outputSummary(call)
  // rawInput 的 JSON 序列化只在展开态付出；两块长文本都经
  // StudioChatTruncatedPre 渲染截断（#1120），点开出口才付全量 DOM
  // 成本——call 本身持有的数据始终是完整的。
  const inputJson = useMemo(
    () =>
      open && call.rawInput ? JSON.stringify(call.rawInput, null, 2) : null,
    [open, call.rawInput]
  )
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
          {inputJson && (
            <StudioChatTruncatedPre
              text={inputJson}
              expandLabel="查看完整输入"
            />
          )}
          {call.outputText && (
            <StudioChatTruncatedPre
              text={call.outputText}
              expandLabel="查看完整输出"
            />
          )}
        </div>
      )}
    </div>
  )
}
