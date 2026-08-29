import type { WorkflowNodeRecord } from '../../../types'
import {
  parseWorkflowNode,
  patchWorkflowNodeCapability,
  patchWorkflowNodeLabel,
} from '../shared/workflowStudioYamlDraft'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  onDefinitionYamlChange: (nextYaml: string) => void
}

export function WorkflowNodeStructuredEditor({
  node,
  definitionYaml,
  onDefinitionYamlChange,
}: Props) {
  const draftNode = parseWorkflowNode(definitionYaml, node.key) ?? node
  const label = draftNode.label
  const capability = draftNode.capability ?? node.capability
  const handleLabelChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeLabel(definitionYaml, node.key, event.target.value)
    )
  const handleCapabilityChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeCapability(definitionYaml, node.key, event.target.value)
    )

  return (
    <section
      aria-label="Workflow node structured editor"
      className={styles.structuredSection}
    >
      <h3 className={styles.structuredTitle}>基本设置</h3>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>节点名称</span>
        <input
          aria-label="节点名称"
          className={styles.fieldInput}
          value={label}
          onChange={handleLabelChange}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>能力 Key</span>
        <input
          aria-label="能力"
          className={styles.fieldInput}
          value={capability}
          onChange={handleCapabilityChange}
        />
      </label>
    </section>
  )
}
