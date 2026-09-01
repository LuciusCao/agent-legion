/**
 * 「定制预览」对话框（issue #328）：复用 workflowStudio/chat 的
 * useStudioChat + 展示组件的薄封装（不改 chat 现有文件）。agent 经 MCP
 * 预览面板工具写草稿，草稿实时渲染在左栏（PreviewPanelSection 负责）；
 * 发布/恢复默认是这里的人工动作（reject_studio_agent_scope 在后端钉死）。
 */
import { useState } from 'react'
import { Button, Dialog, DialogContent, DialogTitle } from '@mui/material'
import { useStudioChat } from '../workflowStudio/chat/useStudioChat'
import { StudioChatSessionBar } from '../workflowStudio/chat/StudioChatSessionBar'
import { StudioChatMessageList } from '../workflowStudio/chat/StudioChatMessageList'
import { StudioChatRunBar } from '../workflowStudio/chat/StudioChatRunBar'
import { StudioChatInput } from '../workflowStudio/chat/StudioChatInput'
import type { PreviewPanelState } from './previewPanelApi'
import {
  useArchivePreviewPanel,
  usePublishPreviewPanel,
} from './usePreviewPanel'
import styles from './PreviewPanelSection.module.css'

export interface CustomizePreviewDialogProps {
  workspaceId: string
  /** 当前面板治理状态（published + draft），由父级轮询刷新。 */
  state: PreviewPanelState | null
  onClose: () => void
}

export function CustomizePreviewDialog({
  workspaceId,
  state,
  onClose,
}: CustomizePreviewDialogProps) {
  const chat = useStudioChat(workspaceId)
  const [chosenAgentId, setChosenAgentId] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const publishMutation = usePublishPreviewPanel(workspaceId)
  const archiveMutation = useArchivePreviewPanel(workspaceId)
  const selectedAgentId = chosenAgentId || (chat.agents[0]?.id ?? '')

  const draft = state?.draft ?? null
  const published = state?.published ?? null

  async function runAction(action: () => Promise<unknown>) {
    setActionError(null)
    try {
      await action()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '操作失败')
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: '720px', width: '95vw' } }}
    >
      <DialogTitle>定制预览面板</DialogTitle>
      <DialogContent className={styles.dialogBody}>
        <div className={styles.hint}>
          让 agent 先读 get_preview_guide 与 get_preview_context
          了解桥协议与真实数据形状；agent 只能写草稿，发布后才会对所有人可见。
        </div>
        {chat.agentsError ? (
          <div className={styles.error}>Agent 列表加载失败，请稍后重试</div>
        ) : !chat.agentsLoading && chat.agents.length === 0 ? (
          <div className={styles.hint}>
            未检测到可用的 ACP agent，请联系管理员配置
          </div>
        ) : (
          <>
            <StudioChatSessionBar
              agents={chat.agents}
              sessions={chat.sessions}
              selectedAgentId={selectedAgentId}
              activeSessionId={chat.activeSessionId}
              onSelectAgent={setChosenAgentId}
              onSelectSession={(sessionId) =>
                void chat.selectSession(sessionId)
              }
              onNewChat={() =>
                selectedAgentId && void chat.startSession(selectedAgentId)
              }
              newChatDisabled={!selectedAgentId || chat.starting}
            />
            <div className={styles.chatArea}>
              {chat.activeSessionId === null ? (
                <div className={styles.hint}>
                  选择 Agent，点「＋ 新对话」开始
                </div>
              ) : (
                <StudioChatMessageList
                  chat={chat}
                  workspaceId={workspaceId}
                  onApplyWorkflowDraft={() => undefined}
                />
              )}
            </div>
            <StudioChatRunBar
              status={chat.session?.status ?? null}
              busy={chat.busy}
              lastRunMs={chat.lastRunMs}
              onCancel={() => void chat.cancel()}
            />
            <StudioChatInput
              busy={chat.busy}
              disabled={!chat.session || chat.closed}
              disabledReason={
                !chat.session
                  ? '先选择会话或新建对话'
                  : chat.closed
                    ? '会话已关闭或中断'
                    : null
              }
              onSend={(text) => void chat.send(text)}
            />
          </>
        )}
        {(actionError || chat.actionError) && (
          <div className={styles.error} role="alert">
            {actionError ?? chat.actionError}
          </div>
        )}
        <div className={styles.footer}>
          <span className={styles.footerStatus}>
            {draft
              ? `草稿 v${draft.version}（${draft.created_by}）`
              : '暂无草稿'}
            {' · '}
            {published
              ? `已发布 v${published.version}`
              : '未发布（当前为默认预览）'}
          </span>
          <Button
            size="small"
            variant="outlined"
            disabled={!published && !draft}
            onClick={() => {
              if (
                window.confirm('恢复默认预览？已发布版本与草稿都会被归档。')
              ) {
                void runAction(() => archiveMutation.mutateAsync())
              }
            }}
          >
            恢复默认
          </Button>
          <Button
            size="small"
            variant="contained"
            disabled={!draft || publishMutation.isPending}
            onClick={() => void runAction(() => publishMutation.mutateAsync())}
          >
            发布草稿
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
