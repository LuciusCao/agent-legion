import { useMemo } from 'react'
import type { StudioChat } from './useStudioChat'
import { useChatAutoScroll } from './useChatAutoScroll'
import {
  asText,
  statusEvent,
  streamingTextId,
  type ToolCallView,
} from './studioChatMessages'
import { supersededCancelRequestIds } from './studioChatCancelVisibility'
import { useQueuedMessageStates } from './studioChatTurnRecovery'
import { MessageItem } from './StudioChatMessageItem'
import { permissionRequestId } from './StudioChatPermission'
import { StudioChatWindow } from './StudioChatWindow'
import styles from './StudioChatPanel.module.css'

type Props = {
  chat: StudioChat
  workspaceId: string
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
}

export function StudioChatMessageList(props: Props) {
  const { chat } = props
  const { bottomRef, listRef, handleScroll, pinnedToBottomRef } =
    useChatAutoScroll(chat.messages)
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
      const id = asText(message.content?.toolCallId)
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
  // #675 codex P2：cancel_requested 行的了结集合——终止事件/新一轮到达后
  // 「等待收尾」降级为历史措辞；派生成每消息布尔再下传，保持 MessageItem
  // memo 的逐行命中（集合身份每次消息变化都会重建）。
  const supersededCancelIds = useMemo(
    () => supersededCancelRequestIds(chat.messages),
    [chat.messages]
  )
  // #882：后端入站排队消息的「已排队 / 未送达」标注（同样逐行下传为标量）。
  const queuedStates = useQueuedMessageStates(chat.messages, chat.closed)
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

  // Updates of an existing tool card have no separate visual row.
  const visibleMessages = chat.messages.filter(
    (message) =>
      (message.kind !== 'tool_call' ||
        toolCallByFirstMessage.has(message.id)) &&
      !(message.kind === 'status' && statusEvent(message).event === 'turn_end')
  )
  return (
    <div
      ref={listRef}
      className={styles.messages}
      aria-label="对话消息"
      onScroll={handleScroll}
    >
      <StudioChatWindow
        ids={visibleMessages.map((message) => message.id)}
        scrollRef={listRef}
        pinnedRef={pinnedToBottomRef}
        renderRow={(index) => {
          const message = visibleMessages[index]
          return (
            <MessageItem
              key={message.id}
              message={message}
              streaming={message.id === streamingId}
              queueState={queuedStates.get(message.id) ?? null}
              cancelSuperseded={supersededCancelIds.has(message.id)}
              toolCall={toolCallByFirstMessage.get(message.id) ?? null}
              permission={
                permissionById.get(permissionRequestId(message)) ?? null
              }
              draftAnchorId={draftAnchorId}
              workflowDraft={chat.workflowDraft}
              agentDrafts={chat.agentDrafts}
              nodeDrafts={chat.nodeDrafts}
              allowAllPermissions={chat.session?.allow_all_permissions ?? false}
              permissionDisabled={
                chat.session?.status !== 'awaiting_permission'
              }
              workspaceId={props.workspaceId}
              onApplyWorkflowDraft={props.onApplyWorkflowDraft}
              onSelectNode={props.onSelectNode}
              onAnswerPermission={chat.answerPermission}
              onToggleAllowAll={chat.setAllowAll}
            />
          )
        }}
      />
      <div ref={bottomRef} />
    </div>
  )
}
