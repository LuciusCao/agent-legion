import { memo } from 'react'
import {
  permissionResolutionText,
  planEntries,
  textContent,
  type ChatMessage,
  type ToolCallView,
  type WorkflowDraftView,
  type AgentDefinitionDraftView,
  type NodeCodeDraftView,
  type PermissionView,
} from './studioChatMessages'
import { StatusLine } from './StudioChatStatusLine'
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

export type MessageItemProps = {
  message: ChatMessage
  streaming: boolean
  toolCall: ToolCallView | null
  permission: PermissionView | null
  draftAnchorId: string | null
  workflowDraft: WorkflowDraftView | null
  agentDrafts: AgentDefinitionDraftView[]
  nodeDrafts: NodeCodeDraftView[]
  allowAllPermissions: boolean
  permissionDisabled: boolean
  workspaceId: string
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
  onAnswerPermission: (
    requestId: string,
    answer: { option_id?: string; deny?: boolean }
  ) => Promise<void>
  onToggleAllowAll: (enabled: boolean) => Promise<void>
}

// Memoized per message: a streaming update appends/touches one message, and
// the rest of the (potentially long) list should not re-render for it. The
// props are split into per-message scalars / stable collections so the memo
// actually hits; `agentDrafts`/`nodeDrafts`/`workflowDraft` come from derived
// state whose references only change when their content changes.
export const MessageItem = memo(function MessageItem(props: MessageItemProps) {
  const {
    message,
    streaming,
    toolCall,
    permission,
    draftAnchorId,
    workflowDraft,
    agentDrafts,
    nodeDrafts,
    allowAllPermissions,
    permissionDisabled,
    workspaceId,
    onApplyWorkflowDraft,
    onSelectNode,
    onAnswerPermission,
    onToggleAllowAll,
  } = props
  if (message.kind === 'text') {
    // 流式中的 agent 文本保持纯文本；空闲时 busy=false 全部视为已完成
    // （含后端重启丢失 turn_end 的兜底）。
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
        {workflowDraft && draftAnchorId === toolCall.toolCallId && (
          <WorkflowDraftCard
            draft={workflowDraft}
            workspaceId={workspaceId}
            onApply={onApplyWorkflowDraft}
          />
        )}
        {agentDrafts
          .filter((draft) => draft.toolCallId === toolCall.toolCallId)
          .map((draft) => (
            <AgentDefinitionDraftCard key={draft.toolCallId} draft={draft} />
          ))}
        {nodeDrafts
          .filter((draft) => draft.toolCallId === toolCall.toolCallId)
          .map((draft) => (
            <NodeCodeDraftCard
              key={draft.toolCallId}
              draft={draft}
              onSelectNode={onSelectNode}
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
    if (!permission) return null
    return (
      <StudioChatPermission
        permission={permission}
        allowAll={allowAllPermissions}
        disabled={permissionDisabled}
        onAnswer={(requestId, answer) =>
          void onAnswerPermission(requestId, answer)
        }
        onToggleAllowAll={(enabled) => void onToggleAllowAll(enabled)}
      />
    )
  }
  if (message.kind === 'status') {
    return <StatusLine message={message} />
  }
  return null
})
