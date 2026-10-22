import { NodeCodeDraftCard } from './StudioChatDraftCards'
import { WorkflowProposalCards } from './RuntimeConfigCard'
import type {
  MergedNodeCodeDraft,
  MergedWorkflowDraft,
} from './studioChatDraftEvents'
import styles from './StudioChatPanel.module.css'

/** #1123：合成 draft_saved 消息行（kind='draft'）上的草稿卡组。卡片只在
 * 「胜出锚点」消息上渲染——同实体存在更新的 tool_call 证据时该合成消息已被
 * MessageList 过滤，这里的空渲染是双保险。 */
export function DraftMessageCards({
  messageId,
  workflowDraft,
  nodeDrafts,
  workspaceId,
  onApplyWorkflowDraft,
  onSelectNode,
}: {
  messageId: string
  workflowDraft: MergedWorkflowDraft | null
  nodeDrafts: MergedNodeCodeDraft[]
  workspaceId: string
  onApplyWorkflowDraft: (yaml: string) => void
  onSelectNode?: (nodeKey: string) => void
}) {
  const anchoredDrafts = nodeDrafts.filter(
    (draft) => draft.anchorMessageId === messageId
  )
  const showWorkflow = workflowDraft?.anchorMessageId === messageId
  if (!showWorkflow && anchoredDrafts.length === 0) return null
  return (
    <div className={styles.toolCallGroup}>
      {showWorkflow && workflowDraft && (
        // M65 拆卡：运行配置卡固定在前、结构草稿卡在后（两卡操作对象
        // 不相交，各自内部判空——无差异/无上下文时自行返回 null）。
        <WorkflowProposalCards
          draft={workflowDraft}
          workspaceId={workspaceId}
          onApply={onApplyWorkflowDraft}
        />
      )}
      {anchoredDrafts.map((draft) => (
        <NodeCodeDraftCard
          key={draft.anchorMessageId ?? draft.toolCallId}
          draft={draft}
          workspaceId={workspaceId}
          onSelectNode={onSelectNode}
        />
      ))}
    </div>
  )
}
