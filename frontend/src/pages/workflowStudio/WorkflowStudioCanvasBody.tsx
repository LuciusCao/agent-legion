import { DagGraph } from '../../components/dag/DagGraph'
import { WorkflowDefinitionEditor } from './WorkflowDefinitionEditor'
import { WorkflowStudioChangesView } from './WorkflowStudioChangesView'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import styles from './WorkflowStudioChangesView.module.css'

/** 画布区三模式主体：DAG 画布 | YAML 编辑 | 变更与校验。 */
export function WorkflowStudioCanvasBody({
  props,
}: {
  props: StudioLayoutProps
}) {
  if (props.canvasMode === 'yaml') {
    return (
      <WorkflowDefinitionEditor
        value={props.definitionYaml}
        onChange={props.setDefinitionYaml}
        readOnly={props.readOnly}
        label={props.readOnly ? 'Revision YAML' : '工作流 YAML'}
      />
    )
  }
  if (props.canvasMode === 'changes') {
    return (
      <WorkflowStudioChangesView
        studio={props}
        onSelectNode={(nodeKey) => {
          props.setSelectedNodeKey(nodeKey)
          props.setCanvasMode('dag')
        }}
      />
    )
  }
  // 无已发布 workflow 时，compare 叠加的 ghost 节点（空态模板草稿）仍可
  // 展示；只有连 ghost 节点都没有时才落到占位文案。
  if (!props.workflow && props.nodes.length === 0) {
    return <p className={styles.empty}>尚未发布 workflow，暂无 DAG 可展示。</p>
  }
  return (
    <DagGraph
      nodes={props.nodes}
      edges={props.edges}
      selectedNode={props.selectedNodeKey}
      onSelectedNodeChange={props.setSelectedNodeKey}
      hideNodeDetails
    />
  )
}
