import { WorkflowStudioCanvasPanel } from '../canvas/WorkflowStudioCanvasPanel'
import { WorkflowStudioDetailSection } from '../inspector/WorkflowStudioDetailSection'
import { WorkflowStudioResizeHandle } from './WorkflowStudioResizeHandle'
import { StudioChatDock } from '../chat/StudioChatDock'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'
import { useStudioState } from './studioStateContext'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'

type Props = {
  mobilePanel: StudioMobilePanel
  agentOpen: boolean
}

/** 画布 + 节点详情分栏 grid；Agent 对话已迁入 AgentPanelDock 浮层
 * （#795 PR②，盖在 DAG 上、不占 grid 轨道）——DAG 区默认占满全宽，
 * 分栏只由节点详情驱动。agentOpen 来自 StudioViewContext（appbar 开关的
 * 唯一状态源，#668）：开 = Dock 挂载（展开或按记忆折叠为小条），关 =
 * 卸载。 */
export function WorkflowStudioSplitLayout({ mobilePanel, agentOpen }: Props) {
  const studio = useStudioState()
  const nodeSelected = studio.selectedNodeKey !== null

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
      {agentOpen && <StudioChatDock />}
    </div>
  )
}
