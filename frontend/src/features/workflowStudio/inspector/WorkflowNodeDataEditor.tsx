import type { WorkflowNodeRecord } from '../../../types'
import {
  parseWorkflowNode,
  patchWorkflowNodeInputs,
  patchWorkflowNodeOutputs,
  patchWorkflowNodeTerminalOutcome,
} from '../shared/workflowStudioYamlDraft'
import { formatLines, parseLines } from './WorkflowNodeStructuredEditor.helpers'
import styles from './WorkflowStructuredEditor.module.css'

export function WorkflowNodeDataEditor(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
}) {
  const draft =
    parseWorkflowNode(props.definitionYaml, props.node.key) ?? props.node
  const patchLines = (
    patcher: typeof patchWorkflowNodeInputs,
    event: React.ChangeEvent<HTMLTextAreaElement>
  ) =>
    props.setDefinitionYaml(
      patcher(
        props.definitionYaml,
        props.node.key,
        parseLines(event.target.value)
      )
    )
  return (
    <div className={styles.fieldStack}>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输入产物，每行一个</span>
        <textarea
          aria-label="输入产物，每行一个"
          className={styles.fieldInput}
          value={formatLines(draft.inputs ?? [])}
          onChange={(event) => patchLines(patchWorkflowNodeInputs, event)}
          rows={Math.max(2, draft.inputs?.length ?? 0)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输出产物，每行一个</span>
        <textarea
          aria-label="输出产物，每行一个"
          className={styles.fieldInput}
          value={formatLines(draft.outputs ?? [])}
          onChange={(event) => patchLines(patchWorkflowNodeOutputs, event)}
          rows={Math.max(2, draft.outputs?.length ?? 0)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Terminal Outcome</span>
        <input
          aria-label="Terminal Outcome"
          className={styles.fieldInput}
          value={draft.terminal?.outcome ?? ''}
          onChange={(event) =>
            props.setDefinitionYaml(
              patchWorkflowNodeTerminalOutcome(
                props.definitionYaml,
                props.node.key,
                event.target.value
              )
            )
          }
        />
      </label>
    </div>
  )
}
