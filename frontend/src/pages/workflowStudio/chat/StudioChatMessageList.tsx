import type { StudioChat } from './useStudioChat'
import { useChatAutoScroll } from './useChatAutoScroll'
import {
  permissionResolutionText,
  planEntries,
  statusEvent,
  streamingTextId,
  textContent,
  type ChatMessage,
  type ToolCallView,
} from './studioChatMessages'
import { StudioChatTextBubble } from './StudioChatTextBubble'
import { StudioChatToolCallCard } from './StudioChatToolCallCard'
import { StudioChatPermission } from './StudioChatPermission'
import { StudioChatThought } from './StudioChatThought'
import {
  AgentDefinitionDraftCard,
  NodeCodeDraftCard,
  WorkflowDraftCard,
} from './StudioChatDraftCards'
import styles from './StudioChatPanel.module.css'

type Props = {
  chat: StudioChat
  workspaceId: string
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
}

function StatusLine({ message }: { message: ChatMessage }) {
  const { event, detail } = statusEvent(message)
  if (event === 'turn_end') return null
  if (event === 'mcp_unverified') {
    return (
      <div className={styles.statusWarning} role="alert">
        ⚠ 本轮没有调用任何 agent-legion 平台工具，agent 可能没有拿到 MCP
        工具，产出请人工核对。{detail}
      </div>
    )
  }
  if (event === 'error') {
    return (
      <div className={styles.statusWarning} role="alert">
        ⚠ {detail || 'agent 运行出错'}
      </div>
    )
  }
  const text =
    event === 'cancel_requested'
      ? '已请求取消当前运行'
      : event === 'session_closed'
        ? '会话已关闭'
        : detail || event
  return <div className={styles.statusLine}>{text}</div>
}

export function StudioChatMessageList(props: Props) {
  const { chat } = props
  const { bottomRef, listRef, handleScroll } = useChatAutoScroll(chat.messages)
  const toolCallByFirstMessage = new Map<string, ToolCallView>()
  const seen = new Set<string>()
  for (const message of chat.messages) {
    if (message.kind !== 'tool_call') continue
    const call = chat.toolCalls.find(
      (view) => view.toolCallId === toolCallIdOf(message)
    )
    const id = toolCallIdOf(message)
    if (id && call && !seen.has(id)) {
      seen.add(id)
      toolCallByFirstMessage.set(message.id, call)
    }
  }

  // workflow 草稿卡片挂在最后一个携带该 yaml 的工具调用后面。
  const draftAnchorId = chat.workflowDraft
    ? (chat.toolCalls
        .filter(
          (call) =>
            call.rawInput?.definition_yaml === chat.workflowDraft!.yaml &&
            (call.title.toLowerCase().includes('validate_workflow') ||
              call.title.toLowerCase().includes('compare_workflow'))
        )
        .slice(-1)[0]?.toolCallId ?? null)
    : null

  return (
    <div
      ref={listRef}
      className={styles.messages}
      aria-label="对话消息"
      onScroll={handleScroll}
    >
      {chat.messages.map((message) => (
        <MessageItem
          key={message.id}
          message={message}
          props={props}
          toolCall={toolCallByFirstMessage.get(message.id) ?? null}
          draftAnchorId={draftAnchorId}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

function toolCallIdOf(message: ChatMessage): string {
  const content = message.content as Record<string, unknown>
  return typeof content?.toolCallId === 'string' ? content.toolCallId : ''
}

function MessageItem({
  message,
  props,
  toolCall,
  draftAnchorId,
}: {
  message: ChatMessage
  props: Props
  toolCall: ToolCallView | null
  draftAnchorId: string | null
}) {
  const { chat } = props
  if (message.kind === 'text') {
    // 流式中的 agent 文本保持纯文本；空闲时 busy=false 全部视为已完成
    // （含后端重启丢失 turn_end 的兜底）。
    const streaming = chat.busy && message.id === streamingTextId(chat.messages)
    return <StudioChatTextBubble message={message} streaming={streaming} />
  }
  if (message.kind === 'thought') {
    return <StudioChatThought text={textContent(message)} />
  }
  if (message.kind === 'tool_call') {
    if (!toolCall) return null
    return (
      <div className={styles.toolCallGroup}>
        <StudioChatToolCallCard call={toolCall} />
        {chat.workflowDraft && draftAnchorId === toolCall.toolCallId && (
          <WorkflowDraftCard
            draft={chat.workflowDraft}
            workspaceId={props.workspaceId}
            onApply={props.onApplyWorkflowDraft}
          />
        )}
        {chat.agentDrafts
          .filter((draft) => draft.toolCallId === toolCall.toolCallId)
          .map((draft) => (
            <AgentDefinitionDraftCard key={draft.toolCallId} draft={draft} />
          ))}
        {chat.nodeDrafts
          .filter((draft) => draft.toolCallId === toolCall.toolCallId)
          .map((draft) => (
            <NodeCodeDraftCard
              key={draft.toolCallId}
              draft={draft}
              onSelectNode={props.onSelectNode}
            />
          ))}
      </div>
    )
  }
  if (message.kind === 'plan') {
    const entries = planEntries(message)
    if (entries.length === 0) return null
    return (
      <div className={styles.plan}>
        <div className={styles.planTitle}>计划</div>
        <ul>
          {entries.map((entry, index) => (
            <li key={index} data-status={entry.status || undefined}>
              {entry.content}
            </li>
          ))}
        </ul>
      </div>
    )
  }
  if (message.kind === 'permission') {
    const content = message.content as Record<string, unknown>
    if (content?.status === 'resolved' && !content?.request_id) {
      const text = permissionResolutionText(message)
      return text ? <div className={styles.statusLine}>{text}</div> : null
    }
    if (content?.status === 'resolved') return null
    const permission = chat.permissions.find(
      (view) => view.requestId === content?.request_id
    )
    if (!permission) return null
    return (
      <StudioChatPermission
        permission={permission}
        allowAll={chat.session?.allow_all_permissions ?? false}
        disabled={chat.session?.status !== 'awaiting_permission'}
        onAnswer={(requestId, answer) =>
          void chat.answerPermission(requestId, answer)
        }
        onToggleAllowAll={(enabled) => void chat.setAllowAll(enabled)}
      />
    )
  }
  if (message.kind === 'status') {
    return <StatusLine message={message} />
  }
  return null
}
