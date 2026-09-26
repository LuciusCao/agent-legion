import type { ReactNode } from 'react'
import type { StudioChat } from './useStudioChat'
import { useStudioChatQueue } from './useStudioChatQueue'
import { StudioChatMessageList } from './StudioChatMessageList'
import { AgentChatStatusStrip } from './AgentChatStatusStrip'
import { contextUsageFromSession } from './StudioChatContextRing'
import { StudioChatQueueBar } from './StudioChatQueueBar'
import { StudioChatComposer } from './StudioChatComposer'
import styles from './AgentChatPanel.module.css'

type Props = {
  chat: StudioChat
  workspaceId: string
  /** 面板顶部插槽（会话管理条等）。job 排查不传——会话自动创建，不暴露选择。 */
  header?: ReactNode
  /** composer 工具行注入执行配置芯片（权限模式/模型/思考档位，#658/#695 R4，
   * 仅 Studio 传；diagnosis/preview 的工具行只有发送按钮）。 */
  showAgentConfig?: boolean
  /** 消息列表与运行条之间的插槽（job 排查的动作确认卡区）。 */
  actionArea?: ReactNode
  /** 无激活会话时的占位内容。 */
  emptyState: ReactNode
  /** 会话引导失败条（琥珀，带重试按钮）；展示期间压制 actionError 条。 */
  bootstrapError?: { message: string; onRetry: () => void } | null
  /** chat.actionError 错误条色调：error=红（默认）/ warning=琥珀。 */
  actionErrorTone?: 'error' | 'warning'
  /** 输入禁用文案：无会话 / 会话已关闭两种。 */
  noSessionReason: string
  closedReason: string
  /** 布局变体类（对话框场景补 gap / min-height 等）。 */
  className?: string
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
}

/** 三处 agent 对话界面共用的对话骨架（#695）：消息列表 + 运行状态行 +
 * 队列详情行 + composer 输入卡片（上下文用量为工具行圆环；#695 R3 由
 * ContextMeter/RunBar/ResumeBar 收敛，R4 composer 一体化；#787 状态行从卡内
 * 移回卡外——取消是破坏性动作，不放进输入卡片），发送队列在此内部接线
 * （busy 时发送入队而非直发撞后端单 turn 原子认领的 409）。差异点全部经
 * 插槽/参数注入；agent 列表守卫与各载体的治理面（发布/归档等）留在调用方。 */
export function AgentChatPanel(props: Props) {
  const { chat } = props
  // busy（运行中）不再禁用输入：发送会进入前端队列（见 useStudioChatQueue）。
  // #694：压缩窗口内禁用——此刻发出的消息会被 agent 静默排队后丢弃；队列
  // 门控与重发同样吃 compacting（#694 review P2-a）。
  const compacting = chat.session?.compacting ?? false
  const queue = useStudioChatQueue(
    chat.busy,
    compacting,
    chat.activeSessionId,
    chat.send
  )
  const inputDisabled = !chat.session || chat.closed || compacting
  const disabledReason = !chat.session
    ? props.noSessionReason
    : chat.closed
      ? props.closedReason
      : compacting
        ? '正在压缩上下文，完成后即可发送'
        : null

  return (
    <div
      className={
        props.className
          ? `${styles.chatPanel} ${props.className}`
          : styles.chatPanel
      }
    >
      {props.header}
      {props.bootstrapError && (
        <div className={styles.statusWarning} role="alert">
          {props.bootstrapError.message}
          <button
            type="button"
            className={styles.statusRetry}
            onClick={props.bootstrapError.onRetry}
          >
            重试
          </button>
        </div>
      )}
      {chat.actionError && !props.bootstrapError && (
        <div
          className={
            props.actionErrorTone === 'warning'
              ? styles.statusWarning
              : styles.statusError
          }
          role="alert"
        >
          {chat.actionError}
        </div>
      )}
      {chat.activeSessionId === null ? (
        <div className={styles.emptyState}>{props.emptyState}</div>
      ) : (
        <StudioChatMessageList
          chat={chat}
          workspaceId={props.workspaceId}
          onApplyWorkflowDraft={props.onApplyWorkflowDraft}
          onSelectNode={props.onSelectNode}
        />
      )}
      {props.actionArea}
      {/* #787：状态行（含取消按钮）在输入卡片外、composer 上方独立成行；
       * 队列详情行仅非空时出现。 */}
      <AgentChatStatusStrip chat={chat} queue={queue} />
      <StudioChatQueueBar queue={queue} />
      <StudioChatComposer
        busy={chat.busy}
        disabled={inputDisabled}
        disabledReason={disabledReason}
        onSend={queue.submit}
        config={
          props.showAgentConfig
            ? { workspaceId: props.workspaceId, session: chat.session }
            : undefined
        }
        usage={contextUsageFromSession(chat.session)}
        compacting={compacting}
      />
    </div>
  )
}
