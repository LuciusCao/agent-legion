/**
 * 「定制预览」Dock 面板（issue #328；#795 PR① 从右侧 MUI Dialog 迁移进
 * AgentPanelDock）：复用 workflowStudio/chat 的 useStudioChat + AgentChatPanel
 * 骨架（#695）的薄封装——本组件就是 AgentPanelDock + AgentChatPanel（定制
 * 预览会话）+ 治理 footer 的组合，不在面板内长私有 UI。agent 经 MCP 预览
 * 面板工具写草稿，发布/恢复默认是这里的人工动作（reject_studio_agent_scope
 * 在后端钉死）。草稿**不自动执行**（#347 P1）：agent（或提示注入产物）写入的
 * HTML 未经发布即作为 srcDoc 运行是风险放大器——草稿需经「预览此草稿」
 * 显式动作逐次放行（重开面板回到默认态）。
 * #796 验收返工：面板内不再内嵌草稿预览区（#615 的 CustomizePreviewPane
 * 已撤）——草稿的渲染目标是 job detail 左栏既有预览通道
 * （PreviewPanelSection 的 PreviewPanelHost，与已发布版本同一挂载点、同一
 * 授权判定 previewDraft）：Dock 打开期间点「预览此草稿」，草稿直接在左栏
 * 渲染，随轮询「改一版看一版」；Dock 折叠/关闭不改变左栏既有语义。
 */
import { useState } from 'react'
import { Button } from '@mui/material'
import { AgentPanelDock } from '../agentPanelDock/AgentPanelDock'
import { useStudioChat } from '../workflowStudio/chat/useStudioChat'
import { AgentChatPanel } from '../workflowStudio/chat/AgentChatPanel'
import { StudioChatSessionBar } from '../workflowStudio/chat/StudioChatSessionBar'
import type { PreviewPanelState } from './previewPanelApi'
import {
  useArchivePreviewPanel,
  usePublishPreviewPanel,
} from './usePreviewPanel'
import styles from './CustomizePreviewDock.module.css'

export interface CustomizePreviewDockProps {
  workspaceId: string
  /** 当前面板治理状态（published + draft），由父级轮询刷新。 */
  state: PreviewPanelState | null
  /** 草稿预览是否已获逐次授权（左栏渲染共用的判定，父级持有）。 */
  previewDraft: boolean
  onPreviewDraft: () => void
  onClose: () => void
}

export function CustomizePreviewDock({
  workspaceId,
  state,
  previewDraft,
  onPreviewDraft,
  onClose,
}: CustomizePreviewDockProps) {
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
    <AgentPanelDock
      surfaceKey="customize-preview"
      title="定制预览面板"
      defaultSize={{ width: 480, height: 620 }}
      minWidth={340}
      onClose={onClose}
    >
      <div className={styles.body}>
        <div className={styles.chatColumn}>
          <div className={styles.hint}>
            让 agent 先读 get_preview_guide 与 get_preview_context
            了解桥协议与真实数据形状；agent 只能写草稿，点「预览此草稿」后
            草稿在左栏预览区渲染（仅本页可见），发布后才会对所有人可见。
          </div>
          {chat.agentsError ? (
            <div className={styles.error}>Agent 列表加载失败，请稍后重试</div>
          ) : !chat.agentsLoading && chat.agents.length === 0 ? (
            <div className={styles.hint}>
              未检测到可用的 ACP agent，请联系管理员配置
            </div>
          ) : (
            <AgentChatPanel
              chat={chat}
              workspaceId={workspaceId}
              className={styles.chatArea}
              header={
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
              }
              emptyState="选择 Agent，点「＋ 新对话」开始"
              noSessionReason="先选择会话或新建对话"
              closedReason="会话已关闭或中断，点「继续对话」恢复"
              onApplyWorkflowDraft={() => undefined}
            />
          )}
          {actionError && (
            <div className={styles.error} role="alert">
              {actionError}
            </div>
          )}
        </div>
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
            variant={previewDraft ? 'contained' : 'outlined'}
            color={previewDraft ? 'warning' : 'primary'}
            disabled={!draft}
            onClick={onPreviewDraft}
          >
            {previewDraft ? '预览草稿中' : '预览此草稿'}
          </Button>
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
      </div>
    </AgentPanelDock>
  )
}
