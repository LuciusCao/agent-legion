import { useState } from 'react'
import type { ReactNode } from 'react'
import styles from './WorkflowStructuredEditor.module.css'

// 文本/数字值的失焦提交输入框（#428 独立复审 P2-3，从
// WorkflowNodeConfigValues 拆出守单文件预算）：本地持有原始串，onBlur
// 才 parse 落草稿——onChange 立即 Number() 会把 '1.' 解析回 '1'，小数点
// 被吃掉。失焦时值未变不提交；非法输入由上层「不落草稿」回弹到落盘值
// 并经 error 显示行内错误（enum/边界校验在 #428 codex P1-B 接入）。
export function NumberOrTextValueField({
  fieldKey,
  label,
  raw,
  readOnly,
  onCommit,
  error,
}: {
  fieldKey: string
  label: ReactNode
  raw: string
  readOnly?: boolean
  onCommit: (next: string) => void
  error?: string
}) {
  const [draft, setDraft] = useState(raw)
  const [focused, setFocused] = useState(false)
  const value = focused ? draft : raw
  return (
    <label className={styles.field}>
      {label}
      <input
        aria-label={`版本值 ${fieldKey}`}
        className={styles.fieldInput}
        value={value}
        disabled={readOnly}
        placeholder="（Schema 默认）"
        onFocus={(event) => {
          setFocused(true)
          setDraft(event.target.value)
        }}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          setFocused(false)
          if (draft !== raw) onCommit(draft)
        }}
      />
      {error && (
        <span className={styles.fieldHint} role="alert">
          {error}
        </span>
      )}
    </label>
  )
}
