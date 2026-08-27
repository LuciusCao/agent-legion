import { useState } from 'react'
import styles from './StudioChatPanel.module.css'

type Props = {
  busy: boolean
  disabled: boolean
  disabledReason: string | null
  onSend: (text: string) => void
}

export function StudioChatInput(props: Props) {
  const [text, setText] = useState('')

  function submit() {
    const value = text.trim()
    if (!value || props.disabled) return
    props.onSend(value)
    setText('')
  }

  return (
    <div className={styles.inputArea}>
      <div className={styles.inputBox}>
        <textarea
          aria-label="消息输入"
          placeholder={
            props.disabledReason ?? '描述你想调整的 workflow / agent / 节点…'
          }
          value={text}
          disabled={props.disabled}
          rows={2}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // 中文输入法组合中的回车是确认候选，不能当成发送。
            if (
              event.key === 'Enter' &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault()
              submit()
            }
          }}
        />
        <button
          type="button"
          className={styles.sendButton}
          disabled={props.disabled || !text.trim()}
          onClick={submit}
        >
          {props.busy ? '排队' : '发送'}
        </button>
      </div>
      <div className={styles.inputHint}>
        Enter 发送 · Shift+Enter 换行 · 运行中发送将进入队列
      </div>
    </div>
  )
}
