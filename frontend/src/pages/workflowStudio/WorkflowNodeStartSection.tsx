import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { WorkflowNodeDependencySection } from './WorkflowNodeDependencySection'
import inspectorStyles from './WorkflowNodeInspector.module.css'

// Start nodes carry the entry contract (type: start) and never execute: the
// capability/execution/code editors do not apply (the backend 404s their
// node-code endpoints), so the inspector shows the read-only contract plus
// the structural dependency info.
export function WorkflowNodeStartSection(props: {
  details: SelectedWorkflowNodeDetails
}) {
  const { node } = props.details
  const types = (node.accepted_item_types ?? []).join('、') || '（未声明）'
  return (
    <>
      <section className={inspectorStyles.section} aria-label="入口节点">
        <div className={inspectorStyles.sectionTitle}>入口节点</div>
        <div className={inspectorStyles.value}>
          该节点是 workflow 入口（type: start），永不执行；接受的条目类型：
          {types}
        </div>
      </section>
      <WorkflowNodeDependencySection
        key={`dependencies-${node.key}`}
        details={props.details}
      />
    </>
  )
}
