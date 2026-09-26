import { useState } from 'react'
import type { ReactNode } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'
import { StudioChatCancelButton } from './StudioChatCancelButton'
import { StudioChatComposerConfig } from './StudioChatComposerConfig'
import { StudioChatContextRing } from './StudioChatContextRing'
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
  /** 会话状态行（运行/排队摘要/恢复入口，AgentChatStatusStrip），渲染为卡内
   * textarea 与工具行之间的一行；为 null（无状态可显示）时不占行。 */
  statusSlot?: ReactNode
  /** 上下文用量：工具行右侧的圆环占比，hover 显示精确值；无数据不渲染。 */
  usage?: { used: number | null; size: number | null } | null
  /** 压缩窗口内圆环脉冲提示（发送禁用仍由 disabled/disabledReason 承担）。 */
  compacting?: boolean
  /** #787：取消当前运行。传入即在工具行发送/排队按钮左侧渲染次级描边的
   * 取消按钮；不运行时传 undefined，完全不占位。 */
  onCancel?: () => void
}

/** 三处 agent 对话共用的 composer（#695 R4）：圆角卡片一体化——上半 textarea，
 * 卡内底部工具行（左：权限模式芯片；右：上下文圆环 + 模型/思考档位芯片 +
 * 发送按钮），状态行经 statusSlot 收进卡内（textarea 与工具行之间，#787 起
 * 只保留状态文本——取消按钮移到工具行发送/排队按钮旁，仅运行中显示）。
 * 配置控件与状态都是输入框容器内的行内元素，不再独立占行；快捷键提示并入
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

  // 圆环位于右组最左（模型芯片左边）：有配置芯片时经 contextRing 注入右组，
  // 无配置面时直接落在 spacer 与发送按钮之间。
  const contextRing = (
    <StudioChatContextRing
      used={props.usage?.used ?? null}
      size={props.usage?.size ?? null}
      compacting={props.compacting ?? false}
    />
  )

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
          rows={3}
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
        {props.statusSlot}
        <div className={styles.toolbar}>
          {props.config ? (
            <StudioChatComposerConfig
              workspaceId={props.config.workspaceId}
              session={props.config.session}
              contextRing={contextRing}
            />
          ) : (
            <>
              <span className={styles.toolbarSpacer} />
              {contextRing}
            </>
          )}
          {props.onCancel && (
            <StudioChatCancelButton onCancel={props.onCancel} />
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
