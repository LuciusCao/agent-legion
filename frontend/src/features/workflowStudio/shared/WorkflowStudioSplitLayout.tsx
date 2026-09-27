import { WorkflowStudioCanvasPanel } from '../canvas/WorkflowStudioCanvasPanel'
import { WorkflowStudioDetailSection } from '../inspector/WorkflowStudioDetailSection'
import { WorkflowStudioResizeHandle } from './WorkflowStudioResizeHandle'
import { StudioChatDock } from '../chat/StudioChatDock'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'
import { useStudioState } from './studioStateContext'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'

type Props = {
  mobilePanel: StudioMobilePanel
  agentOpen: boolean
}

/** 画布 + 节点详情分栏 grid；Agent 对话已迁入 AgentPanelDock 浮层
 * （#795 PR②，盖在 DAG 上、不占 grid 轨道）——DAG 区默认占满全宽，
 * 分栏只由节点详情驱动。agentOpen 来自 StudioViewContext（appbar 开关的
 * 唯一状态源，#668）。Dock 常驻挂载、关闭/窄屏未选中 Agent 页签时
 * 隐藏不卸载（hidden——composer 文本/发送队列/SSE 不因显隐断开，#797
 * codex P1）；窄屏下可见性由 mobilePanel 参与决定（仅 Agent 页签选中时
 * 显示，避免手机首进被浮层抢占画布，#797 codex P2）。 */
export function WorkflowStudioSplitLayout({ mobilePanel, agentOpen }: Props) {
  const studio = useStudioState()
  const nodeSelected = studio.selectedNodeKey !== null
  const narrow = useStudioNarrowViewport()
  const dockHidden = !agentOpen || (narrow && mobilePanel !== 'agent')

  return (
    <div
      className={`${pageStyles.layout}${nodeSelected ? ` ${pageStyles.withInspector}` : ''}`}
    >
      <WorkflowStudioCanvasPanel mobileActive={mobilePanel === 'graph'} />
      {nodeSelected && <WorkflowStudioResizeHandle />}
      {studio.selectedNodeKey !== null && (
        <WorkflowStudioDetailSection
          workflow={studio.workflow}
          nodeKey={studio.selectedNodeKey}
          agentCatalog={studio.agentCatalog}
          agentCatalogSettle={studio.agentCatalogSettle}
          definitionYaml={studio.definitionYaml}
          setDefinitionYaml={studio.setDefinitionYaml}
          compareSummary={studio.compareSummary}
          readOnly={studio.readOnly}
          mobileActive={mobilePanel === 'editor'}
          onBack={() => studio.setSelectedNodeKey(null)}
        />
      )}
      <StudioChatDock hidden={dockHidden} />
    </div>
  )
}
