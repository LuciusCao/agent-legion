/**
 * 「定制预览」覆盖面板（issue #328 / #615 方向 A）：复用 workflowStudio/chat
 * 的 useStudioChat + AgentChatPanel 骨架（#695）的薄封装。agent 经 MCP
 * 预览面板工具写草稿，发布/恢复默认是这里的人工动作（reject_studio_agent_scope
 * 在后端钉死）。草稿**不自动执行**（#347 P1）：agent（或提示注入产物）写入的
 * HTML 未经发布即作为 srcDoc 运行是风险放大器——草稿需经「预览此草稿」显式
 * 动作逐次放行（重开面板回到默认态），渲染目标只有左栏既有通道
 * （PreviewPanelHost），面板内不内嵌预览。
 * #615 方向 A（推翻 #701 的对话框内嵌预览）：面板是**非模态覆盖层**，宽屏
 * 停靠右侧盖住 job progress 列，不遮挡左栏预览区、不锁底层滚动——
 * 「agent 改草稿 → 人工看左栏预览 → 继续对话」闭环不离开页面。窄屏
 * （<1200px）降级为右下浮动卡片，并可折叠为右下角小条（定位契约见
 * customizePreviewOverlaySx）。
 * codex 复审两条 P2 的落地形态：
 * - P2-B：不走 MUI Dialog/Modal——ModalManager 会把 portal 外的应用内容打
 *   进 aria-hidden 且 Dialog 声明 aria-modal=true，读屏器场景下「左栏全程
 *   可交互」不成立。改用 Portal + Paper 的自定义 modeless surface
 *   （role="dialog" + aria-modal="false"）；Escape 关闭（焦点在面板内时，
 *   与 MUI Modal 的行为域一致）与焦点移交/还原由本组件自实现，无遮罩、
 *   不锁滚动、不圈禁焦点是有意语义。
 * - P2-A：折叠只 display:none 隐藏内容、不卸载聊天子树——排队消息与未发送
 *   输入保存在 AgentChatPanel/Composer 的组件本地 state 里，卸载即静默
 *   清空。
 */
import { useEffect, useRef, useState } from 'react'
import { IconButton, Paper, Portal, Tooltip } from '@mui/material'
import { Close, UnfoldLess } from '@mui/icons-material'
import { useStudioChat } from '../workflowStudio/chat/useStudioChat'
import { AgentChatPanel } from '../workflowStudio/chat/AgentChatPanel'
import { StudioChatSessionBar } from '../workflowStudio/chat/StudioChatSessionBar'
import type { PreviewPanelState } from './previewPanelApi'
import { CustomizePreviewFooter } from './CustomizePreviewFooter'
import { overlaySurfaceSx } from './customizePreviewOverlaySx'
import {
  useArchivePreviewPanel,
  usePublishPreviewPanel,
} from './usePreviewPanel'
import styles from './CustomizePreviewDialog.module.css'

export interface CustomizePreviewDialogProps {
  workspaceId: string
  /** 当前面板治理状态（published + draft），由父级轮询刷新。 */
  state: PreviewPanelState | null
  /** 草稿预览是否已获逐次授权（与左栏渲染共用同一判定，父级持有）。 */
  previewDraft: boolean
  onPreviewDraft: () => void
  onClose: () => void
}

export function CustomizePreviewDialog({
  workspaceId,
  state,
  previewDraft,
  onPreviewDraft,
  onClose,
}: CustomizePreviewDialogProps) {
  const chat = useStudioChat(workspaceId)
  const [chosenAgentId, setChosenAgentId] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const publishMutation = usePublishPreviewPanel(workspaceId)
  const archiveMutation = useArchivePreviewPanel(workspaceId)
  const selectedAgentId = chosenAgentId || (chat.agents[0]?.id ?? '')
  const surfaceRef = useRef<HTMLDivElement>(null)

  // modeless dialog 惯例：打开时把焦点交给面板（键盘用户立即可用 Escape
  // 关闭），卸载时还原给触发点；preventScroll 防焦点驱动的页面跳动。
  useEffect(() => {
    const previous = document.activeElement
    surfaceRef.current?.focus({ preventScroll: true })
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus({ preventScroll: true })
      }
    }
  }, [])

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
    <Portal>
      <Paper
        ref={surfaceRef}
        role="dialog"
        aria-modal="false"
        aria-label="定制预览面板"
        tabIndex={-1}
        elevation={8}
        sx={overlaySurfaceSx(collapsed)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose()
        }}
      >
        {/* P2-A：折叠只隐藏（display:none）不卸载——队列与未发送输入保存在
            聊天子树的组件本地 state 里，卸载即静默清空。 */}
        <div className={collapsed ? styles.contentHidden : styles.content}>
          <div className={styles.titleRow}>
            <span className={styles.titleText}>定制预览面板</span>
            <Tooltip title="折叠为右下角小条（对话保持）">
              <IconButton
                size="small"
                aria-label="折叠对话"
                onClick={() => setCollapsed(true)}
              >
                <UnfoldLess fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="关闭">
              <IconButton size="small" aria-label="关闭" onClick={onClose}>
                <Close fontSize="small" />
              </IconButton>
            </Tooltip>
          </div>
          <div className={styles.body}>
            <div className={styles.chatColumn}>
              <div className={styles.hint}>
                让 agent 先读 get_preview_guide 与 get_preview_context
                了解桥协议与真实数据形状；agent 只能写草稿，点「预览此草稿」后
                草稿在左栏渲染并高亮定位（仅本页可见，本面板不遮挡左栏），
                发布后才会对所有人可见。
              </div>
              {chat.agentsError ? (
                <div className={styles.error}>
                  Agent 列表加载失败，请稍后重试
                </div>
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
                        selectedAgentId &&
                        void chat.startSession(selectedAgentId)
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
            <CustomizePreviewFooter
              draft={draft}
              published={published}
              previewDraft={previewDraft}
              publishing={publishMutation.isPending}
              onPreviewDraft={onPreviewDraft}
              onPublish={() =>
                void runAction(() => publishMutation.mutateAsync())
              }
              onArchive={() =>
                void runAction(() => archiveMutation.mutateAsync())
              }
            />
          </div>
        </div>
        {collapsed && (
          <button
            type="button"
            className={styles.collapsedPill}
            onClick={() => setCollapsed(false)}
          >
            定制预览对话（已折叠，点击展开）
          </button>
        )}
      </Paper>
    </Portal>
  )
}
