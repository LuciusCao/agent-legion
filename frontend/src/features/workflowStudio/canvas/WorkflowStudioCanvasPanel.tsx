import { useRef } from 'react'
import { WorkflowStudioCanvasBody } from './WorkflowStudioCanvasBody'
import { WorkflowStudioCanvasSourceBadge } from './WorkflowStudioCanvasSourceBadge'
import { WorkflowStudioCanvasToolbar } from './WorkflowStudioCanvasToolbar'
import { useStudioView } from '../shared/studioStateContext'
import { StudioCanvasIslands } from '../shared/StudioCanvasIslands'
import { useCanvasIslandOffset } from '../shared/useCanvasIslandOffset'
import canvasStyles from '../../../pages/WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../../../pages/WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'
import splitStyles from '../shared/WorkflowStudioSplitLayout.module.css'

type Props = {
  mobileActive: boolean
}

/** 画布区（DAG 常驻，工具栏含编辑 YAML / DAG 全屏；Agent 面板开关在
 * appbar，#668）。#795 PR②：Agent 对话迁入 Dock 浮层后画布不再被替换——
 * 节点详情固定占右栏，画布始终在位。#799：顶边让位按浮动岛实测底边
 * （useCanvasIslandOffset），不写死常量。 */
export function WorkflowStudioCanvasPanel({ mobileActive }: Props) {
  const view = useStudioView()
  const canvasRef = useRef<HTMLElement | null>(null)
  const toolbarTop = useCanvasIslandOffset(canvasRef)
  const className = [
    canvasStyles.canvas,
    splitStyles.colLeft,
    mobileActive ? pageStyles.activePanel : '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <main ref={canvasRef} className={className} data-mobile-panel="graph">
      <div
        data-canvas-toolbar
        className={canvasToolbarStyles.canvasToolbar}
        style={{ top: toolbarTop }}
      >
        <WorkflowStudioCanvasSourceBadge />
        <WorkflowStudioCanvasToolbar
          onEditYaml={() => view.setYamlEditorOpen(true)}
          onDagFullscreen={() => view.setDagFullscreenOpen(true)}
        />
      </div>
      <WorkflowStudioCanvasBody />
      {/* #799 + codex 轮 2 P2：双岛锚定在画布列内（绝对定位相对画布列），
          详情列打开时岛自然不越界；窄屏非画布页签的隐藏由画布列 CSS
          承担（data-mobile-panel display:none）。 */}
      <StudioCanvasIslands />
    </main>
  )
}
