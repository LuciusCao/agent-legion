import { useMemo } from 'react'
import type { StudioChat } from './useStudioChat'
import { useChatAutoScroll } from './useChatAutoScroll'
import {
  streamingTextId,
  type ChatMessage,
  type ToolCallView,
} from './studioChatMessages'
import { MessageItem } from './StudioChatMessageItem'
import styles from './StudioChatPanel.module.css'

type Props = {
  chat: StudioChat
  workspaceId: string
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
}

export function StudioChatMessageList(props: Props) {
  const { chat } = props
  const { bottomRef, listRef, handleScroll } = useChatAutoScroll(chat.messages)
  // Memoized: during streaming every SSE message event re-renders this list;
  // the toolCall lookup is O(messages × toolCalls) if rebuilt naively, and a
  // Map keyed by toolCallId makes it O(messages + toolCalls).
  const toolCallById = useMemo(
    () => new Map(chat.toolCalls.map((view) => [view.toolCallId, view])),
    [chat.toolCalls]
  )
  const toolCallByFirstMessage = useMemo(() => {
    const map = new Map<string, ToolCallView>()
    const seen = new Set<string>()
    for (const message of chat.messages) {
      if (message.kind !== 'tool_call') continue
      const id = toolCallIdOf(message)
      const call = id ? toolCallById.get(id) : undefined
      if (id && call && !seen.has(id)) {
        seen.add(id)
        map.set(message.id, call)
      }
    }
    return map
  }, [chat.messages, toolCallById])
  // Streaming target + permission lookup hoisted from MessageItem: both feed
  // per-message props as primitives / stable view objects, so MessageItem's
  // memo sees unchanged props for untouched messages (a streaming update
  // touches one message; passing the whole `chat` object would defeat memo).
  const streamingId = chat.busy ? streamingTextId(chat.messages) : null
  const permissionById = useMemo(
    () => new Map(chat.permissions.map((view) => [view.requestId, view])),
    [chat.permissions]
  )

  // workflow 草稿卡片挂在最后一个携带该 yaml 的工具调用后面。
  const draftAnchorId = useMemo(
    () =>
      chat.workflowDraft
        ? (chat.toolCalls
            .filter(
              (call) =>
                call.rawInput?.definition_yaml === chat.workflowDraft!.yaml &&
                (call.title.toLowerCase().includes('validate_workflow') ||
                  call.title.toLowerCase().includes('compare_workflow'))
            )
            .slice(-1)[0]?.toolCallId ?? null)
        : null,
    [chat.toolCalls, chat.workflowDraft]
  )

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
          streaming={message.id === streamingId}
          toolCall={toolCallByFirstMessage.get(message.id) ?? null}
          permission={permissionById.get(permissionRequestId(message)) ?? null}
          draftAnchorId={draftAnchorId}
          workflowDraft={chat.workflowDraft}
          agentDrafts={chat.agentDrafts}
          nodeDrafts={chat.nodeDrafts}
          allowAllPermissions={chat.session?.allow_all_permissions ?? false}
          permissionDisabled={chat.session?.status !== 'awaiting_permission'}
          workspaceId={props.workspaceId}
          onApplyWorkflowDraft={props.onApplyWorkflowDraft}
          onSelectNode={props.onSelectNode}
          onAnswerPermission={chat.answerPermission}
          onToggleAllowAll={chat.setAllowAll}
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

function permissionRequestId(message: ChatMessage): string {
  if (message.kind !== 'permission') return ''
  const content = message.content as Record<string, unknown>
  return typeof content?.request_id === 'string' ? content.request_id : ''
}
