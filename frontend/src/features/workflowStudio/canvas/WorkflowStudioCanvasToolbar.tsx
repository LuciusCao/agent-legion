import { Button } from '@mui/material'
import { WorkflowAddNodeButton } from './WorkflowAddNodeButton'
import { WorkflowDagFullscreenButton } from './WorkflowDagFullscreenButton'

type Props = {
  onEditYaml: () => void
  onDagFullscreen: () => void
}

/** 画布工具栏：添加节点（#392 Phase 3）+ 编辑 YAML（打开全屏 Dialog）
 * + DAG 全屏。DAG 是唯一常驻画布视图，不再有模式切换。Agent 面板开关
 * 已收敛到 appbar（CommandBar）唯一入口（#668）。 */
export function WorkflowStudioCanvasToolbar(props: Props) {
  return (
    <>
      <WorkflowAddNodeButton />
      <Button size="small" onClick={props.onEditYaml}>
        编辑 YAML
      </Button>
      <WorkflowDagFullscreenButton onClick={props.onDagFullscreen} />
    </>
  )
}
