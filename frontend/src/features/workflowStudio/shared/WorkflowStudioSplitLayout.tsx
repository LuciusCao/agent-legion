import { WorkflowStudioCanvasPanel } from '../canvas/WorkflowStudioCanvasPanel'
import { WorkflowNodeDetailDrawer } from '../inspector/WorkflowNodeDetailDrawer'
import { StudioChatDock } from '../chat/StudioChatDock'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'

type Props = {
  mobilePanel: StudioMobilePanel
  agentOpen: boolean
}

/** 画布布局（#804 定案抽屉化）：画布永远全宽，节点详情改右侧 Drawer 浮层
 * （WorkflowNodeDetailDrawer，不占 grid 轨道、无分栏/拖拽分隔条）；Agent
 * 对话在 AgentPanelDock 浮层（#795 PR②）。Dock 常驻挂载、关闭/窄屏未选中
 * Agent 页签时隐藏不卸载（hidden——composer 文本/发送队列/SSE 不因显隐
 * 断开，#797 codex P1）；窄屏下 Dock 仅 Agent 页签选中时显示（#797 codex
 * P2）。 */
export function WorkflowStudioSplitLayout({ mobilePanel, agentOpen }: Props) {
  const narrow = useStudioNarrowViewport()
  const dockHidden = !agentOpen || (narrow && mobilePanel !== 'agent')

  return (
    <div className={pageStyles.layout}>
      <WorkflowStudioCanvasPanel mobileActive={mobilePanel === 'graph'} />
      <WorkflowNodeDetailDrawer />
      <StudioChatDock hidden={dockHidden} />
    </div>
  )
}
