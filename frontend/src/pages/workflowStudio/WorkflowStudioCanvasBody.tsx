import { DagGraph } from '../../components/dag/DagGraph'
import { useStudioState } from './studioStateContext'
import styles from './WorkflowStudioChangesView.module.css'

/** 画布主体：DAG 常驻视图；变更在右侧 Drawer、YAML 编辑在全屏 Dialog。 */
export function WorkflowStudioCanvasBody() {
  const studio = useStudioState()
  // 无已发布 workflow 时，compare 叠加的 ghost 节点（空态模板草稿）仍可
  // 展示；只有连 ghost 节点都没有时才落到占位文案。
  if (!studio.workflow && studio.nodes.length === 0) {
    return <p className={styles.empty}>尚未发布 workflow，暂无 DAG 可展示。</p>
  }
  return (
    <DagGraph
      nodes={studio.nodes}
      edges={studio.edges}
      selectedNode={studio.selectedNodeKey}
      onSelectedNodeChange={studio.setSelectedNodeKey}
      hideNodeDetails
    />
  )
}
