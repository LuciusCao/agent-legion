import { DagGraph } from '../../components/dag/DagGraph'
import { WorkflowDefinitionEditor } from './WorkflowDefinitionEditor'
import { WorkflowStudioChangesView } from './WorkflowStudioChangesView'
import { useStudioState, useStudioView } from './studioStateContext'
import styles from './WorkflowStudioChangesView.module.css'

/** 画布区三模式主体：DAG 画布 | YAML 编辑 | 变更与校验。 */
export function WorkflowStudioCanvasBody() {
  const studio = useStudioState()
  const view = useStudioView()
  if (view.canvasMode === 'yaml') {
    return (
      <WorkflowDefinitionEditor
        value={studio.definitionYaml}
        onChange={studio.setDefinitionYaml}
        readOnly={studio.readOnly}
        label={studio.readOnly ? 'Revision YAML' : '工作流 YAML'}
      />
    )
  }
  if (view.canvasMode === 'changes') {
    return (
      <WorkflowStudioChangesView
        studio={studio}
        onSelectNode={(nodeKey) => {
          studio.setSelectedNodeKey(nodeKey)
          view.setCanvasMode('dag')
        }}
      />
    )
  }
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
