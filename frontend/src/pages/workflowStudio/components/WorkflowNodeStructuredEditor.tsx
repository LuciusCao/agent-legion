import type { WorkflowNodeRecord } from '../../../types'
import {
  parseWorkflowNode,
  patchWorkflowNodeCapability,
  patchWorkflowNodeInputs,
  patchWorkflowNodeLabel,
  patchWorkflowNodeOutputs,
  patchWorkflowNodeTerminalOutcome,
} from '../workflowStudioYamlDraft'
import { formatLines, parseLines } from './WorkflowNodeStructuredEditor.helpers'
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
  const inputs = draftNode.inputs ?? []
  const outputs = draftNode.outputs ?? []
  const terminalOutcome = draftNode.terminal?.outcome ?? ''
  const handleLabelChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeLabel(definitionYaml, node.key, event.target.value)
    )
  const handleInputsChange = (event: React.ChangeEvent<HTMLTextAreaElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeInputs(
        definitionYaml,
        node.key,
        parseLines(event.target.value)
      )
    )
  const handleCapabilityChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeCapability(definitionYaml, node.key, event.target.value)
    )
  const handleOutputsChange = (event: React.ChangeEvent<HTMLTextAreaElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeOutputs(
        definitionYaml,
        node.key,
        parseLines(event.target.value)
      )
    )
  const handleTerminalChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeTerminalOutcome(
        definitionYaml,
        node.key,
        event.target.value
      )
    )

  return (
    <section
      aria-label="Workflow node structured editor"
      className={styles.structuredSection}
    >
      <h3 className={styles.structuredTitle}>编辑节点配置</h3>
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
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输入产物，每行一个</span>
        <textarea
          aria-label="输入产物，每行一个"
          className={styles.fieldInput}
          value={formatLines(inputs)}
          onChange={handleInputsChange}
          rows={Math.max(2, inputs.length)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输出产物，每行一个</span>
        <textarea
          aria-label="输出产物，每行一个"
          className={styles.fieldInput}
          value={formatLines(outputs)}
          onChange={handleOutputsChange}
          rows={Math.max(2, outputs.length)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Terminal Outcome</span>
        <input
          aria-label="Terminal Outcome"
          className={styles.fieldInput}
          value={terminalOutcome}
          onChange={handleTerminalChange}
        />
      </label>
    </section>
  )
}
