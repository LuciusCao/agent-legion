import { useState } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'
import { StudioChatComposerConfig } from './StudioChatComposerConfig'
import styles from './StudioChatComposer.module.css'

type Props = {
  busy: boolean
  disabled: boolean
  disabledReason: string | null
  onSend: (text: string) => void
  /** 仅 Studio 传：权限模式/模型/思考档位的紧凑芯片（数据层复用
   * useStudioChatAgentConfig）；diagnosis/preview 不传，工具行只有发送按钮，
   * 不造空槽位。 */
  config?: {
    workspaceId: string | undefined
    session: StudioChatSessionRecord | null
  }
}

/** 三处 agent 对话共用的 composer（#695 R4）：圆角卡片一体化——上半 textarea，
 * 卡内底部工具行（左：权限模式芯片；右：模型/思考档位芯片 + 发送按钮）。
 * 配置控件是输入框容器内的行内元素，不再独立占行；快捷键提示并入
 * placeholder。compacting 禁用、disabledReason、IME 组合守卫、Enter /
 * Shift+Enter 语义与原 StudioChatInput 一致。 */
export function StudioChatComposer(props: Props) {
  const [text, setText] = useState('')

  function submit() {
    const value = text.trim()
    if (!value || props.disabled) return
    props.onSend(value)
    setText('')
  }

  return (
    <div className={styles.composerArea}>
      <div className={styles.composer}>
        <textarea
          aria-label="消息输入"
          placeholder={
            props.disabledReason ??
            '描述你想调整的 workflow / agent / 节点…（Enter 发送 · Shift+Enter 换行 · 运行中发送将进入队列）'
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
        <div className={styles.toolbar}>
          {props.config ? (
            <StudioChatComposerConfig
              workspaceId={props.config.workspaceId}
              session={props.config.session}
            />
          ) : (
            <span className={styles.toolbarSpacer} />
          )}
          <button
            type="button"
            className={styles.sendButton}
            disabled={props.disabled || !text.trim()}
            onClick={submit}
          >
            {props.busy ? '排队' : '发送'}
          </button>
        </div>
      </div>
    </div>
  )
}
