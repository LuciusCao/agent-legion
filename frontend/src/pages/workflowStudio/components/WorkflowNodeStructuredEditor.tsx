import type { WorkflowNodeRecord } from '../../../types'
import {
  patchWorkflowNodeInputs,
  patchWorkflowNodeLabel,
  patchWorkflowNodeOutputs,
  patchWorkflowNodeTerminalOutcome,
} from '../workflowStudioYamlDraft'
import styles from '../WorkflowNodeInspector.module.css'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  onDefinitionYamlChange: (nextYaml: string) => void
}

function parseLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatLines(items: string[]): string {
  return items.join('\n')
}

export function WorkflowNodeStructuredEditor({
  node,
  definitionYaml,
  onDefinitionYamlChange,
}: Props) {
  return (
    <section aria-label="Workflow node structured editor" className={styles.structuredSection}>
      <h3 className={styles.structuredTitle}>结构化编辑</h3>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>节点名称</span>
        <input
          aria-label="节点名称"
          className={styles.fieldInput}
          value={node.label}
          onChange={(event) =>
            onDefinitionYamlChange(
              patchWorkflowNodeLabel(definitionYaml, node.key, event.target.value)
            )
          }
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>能力</span>
        <input
          aria-label="能力"
          className={styles.fieldInput}
          value={node.capability}
          readOnly
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输入产物，每行一个</span>
        <textarea
          aria-label="输入产物，每行一个"
          className={styles.fieldInput}
          value={formatLines(node.inputs)}
          onChange={(event) =>
            onDefinitionYamlChange(
              patchWorkflowNodeInputs(
                definitionYaml,
                node.key,
                parseLines(event.target.value)
              )
            )
          }
          rows={Math.max(2, node.inputs.length)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输出产物，每行一个</span>
        <textarea
          aria-label="输出产物，每行一个"
          className={styles.fieldInput}
          value={formatLines(node.outputs)}
          onChange={(event) =>
            onDefinitionYamlChange(
              patchWorkflowNodeOutputs(
                definitionYaml,
                node.key,
                parseLines(event.target.value)
              )
            )
          }
          rows={Math.max(2, node.outputs.length)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Terminal Outcome</span>
        <input
          aria-label="Terminal Outcome"
          className={styles.fieldInput}
          value={node.terminal?.outcome ?? ''}
          onChange={(event) =>
            onDefinitionYamlChange(
              patchWorkflowNodeTerminalOutcome(
                definitionYaml,
                node.key,
                event.target.value
              )
            )
          }
        />
      </label>
    </section>
  )
}
