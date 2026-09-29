import { WorkflowStudioCanvasBody } from './WorkflowStudioCanvasBody'
import { WorkflowStudioCanvasSourceBadge } from './WorkflowStudioCanvasSourceBadge'
import { WorkflowStudioCanvasToolbar } from './WorkflowStudioCanvasToolbar'
import { useStudioView } from '../shared/studioStateContext'
import canvasStyles from '../../../pages/WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../../../pages/WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'
import splitStyles from '../shared/WorkflowStudioSplitLayout.module.css'

type Props = {
  mobileActive: boolean
}

/** 画布区（DAG 常驻，工具栏含编辑 YAML / DAG 全屏；Agent 面板开关在
 * appbar，#668）。#795 PR②：Agent 对话迁入 Dock 浮层后画布不再被替换——
 * 节点详情固定占右栏，画布始终在位。 */
export function WorkflowStudioCanvasPanel({ mobileActive }: Props) {
  const view = useStudioView()
  const className = [
    canvasStyles.canvas,
    splitStyles.colLeft,
    mobileActive ? pageStyles.activePanel : '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <main className={className} data-mobile-panel="graph">
      <div data-canvas-toolbar className={canvasToolbarStyles.canvasToolbar}>
        <WorkflowStudioCanvasSourceBadge />
        <WorkflowStudioCanvasToolbar
          onEditYaml={() => view.setYamlEditorOpen(true)}
          onDagFullscreen={() => view.setDagFullscreenOpen(true)}
        />
      </div>
      <WorkflowStudioCanvasBody />
    </main>
  )
}
