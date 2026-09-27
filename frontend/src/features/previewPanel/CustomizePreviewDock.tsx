/**
 * 「定制预览」Dock 面板（issue #328；#795 PR① 从右侧 MUI Dialog 迁移进
 * AgentPanelDock）：复用 workflowStudio/chat 的 useStudioChat + AgentChatPanel
 * 骨架（#695）的薄封装。agent 经 MCP 预览面板工具写草稿，发布/恢复默认是
 * 这里的人工动作（reject_studio_agent_scope 在后端钉死）。草稿**不自动执行**
 * （#347 P1）：agent（或提示注入产物）写入的 HTML 未经发布即作为 srcDoc
 * 运行是风险放大器——左栏只渲染已发布版本，草稿需经「预览此草稿」显式动作
 * 逐次放行（重开面板回到默认态）。
 * #615：面板内嵌草稿预览区（CustomizePreviewPane）——预览目标只在面板外时
 * 人工验证事实上不可用；内嵌预览与左栏渲染共用父级的同一授权判定
 * （previewDraft），对话与预览同屏。
 * #795 PR①：容器换成 AgentPanelDock（surface "customize-preview"）——可拖拽/
 * 可缩放/可折叠为右下角小条、位置与折叠态按 surface 记忆、非模态（面板打开
 * 时左栏与 DAG 全程可滚动可交互）；折叠不丢聊天子树状态（Dock 只
 * display:none 不卸载）。容器行为（拖拽/记忆/焦点/Esc）由 agentPanelDock
 * 自己的测试钉住，这里只覆盖治理面与内嵌预览门控。
 */
import { useState } from 'react'
import { Button } from '@mui/material'
import { AgentPanelDock } from '../agentPanelDock/AgentPanelDock'
import { useStudioChat } from '../workflowStudio/chat/useStudioChat'
import { AgentChatPanel } from '../workflowStudio/chat/AgentChatPanel'
import { StudioChatSessionBar } from '../workflowStudio/chat/StudioChatSessionBar'
import type { PreviewPanelState } from './previewPanelApi'
import { CustomizePreviewPane } from './CustomizePreviewPane'
import {
  useArchivePreviewPanel,
  usePublishPreviewPanel,
} from './usePreviewPanel'
import styles from './CustomizePreviewDock.module.css'

export interface CustomizePreviewDockProps {
  workspaceId: string
  /** 当前 job：内嵌预览的桥上下文与重挂 key（与左栏渲染同源）。 */
  jobId: string
  /** 当前面板治理状态（published + draft），由父级轮询刷新。 */
  state: PreviewPanelState | null
  /** 草稿预览是否已获逐次授权（左栏与内嵌预览共用的判定，父级持有）。 */
  previewDraft: boolean
  onPreviewDraft: () => void
  onClose: () => void
}

export function CustomizePreviewDock({
  workspaceId,
  jobId,
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
      defaultSize={{ width: 1000, height: 640 }}
      onClose={onClose}
    >
      <div className={styles.body}>
        <div className={styles.chatColumn}>
          <div className={styles.hint}>
            让 agent 先读 get_preview_guide 与 get_preview_context
            了解桥协议与真实数据形状；agent 只能写草稿，点「预览此草稿」后
            草稿在面板内与左栏同步渲染（仅本页可见），发布后才会对所有人可见。
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
        <CustomizePreviewPane
          jobId={jobId}
          draft={draft}
          previewDraft={previewDraft}
        />
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
