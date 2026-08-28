import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { itemTypeLabels } from '../../lib/acceptedItemTypes'
import { WorkflowNodeDependencySection } from './WorkflowNodeDependencySection'
import { WorkflowNodeStartContractEditor } from './components/WorkflowNodeStartContractEditor'
import inspectorStyles from './WorkflowNodeInspector.module.css'

// Start nodes carry the entry contract (type: start) and never execute: the
// capability/execution/code editors do not apply (the backend 404s their
// node-code endpoints). The inspector shows the entry contract — read-only
// text in readOnly mode, otherwise an accepted_item_types editor (changes go
// through the draft→publish flow) — plus the structural dependency info.
export function WorkflowNodeStartSection(props: {
  details: SelectedWorkflowNodeDetails
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const { node } = props.details
  // readOnly 视图展示用户视角 label（与编辑器、AddItemsDialog 同源），
  // 不拼内部枚举值；未知类型回退原始值，空契约显示占位符。
  const types = itemTypeLabels(node.accepted_item_types ?? [])
  return (
    <>
      <section className={inspectorStyles.section} aria-label="入口节点">
        <div className={inspectorStyles.sectionTitle}>入口节点</div>
        {props.readOnly ? (
          <div className={inspectorStyles.value}>接受条目类型：{types}</div>
        ) : (
          <WorkflowNodeStartContractEditor
            node={node}
            definitionYaml={props.definitionYaml}
            setDefinitionYaml={props.setDefinitionYaml}
          />
        )}
      </section>
      <WorkflowNodeDependencySection
        key={`dependencies-${node.key}`}
        details={props.details}
      />
    </>
  )
}
