import type { WorkflowNodeRecord } from '../../../types'
import {
  parseWorkflowNode,
  patchWorkflowNodeInputs,
  patchWorkflowNodeOutputs,
  patchWorkflowNodeTerminalOutcome,
} from '../shared/workflowStudioYamlDraft'
import { normalizeStringList } from '../shared/workflowStudioYamlDraft.parse'
import { formatLines, parseLines } from './WorkflowNodeStructuredEditor.helpers'
import styles from './WorkflowStructuredEditor.module.css'

export function WorkflowNodeDataEditor(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
}) {
  const draft =
    parseWorkflowNode(props.definitionYaml, props.node.key) ?? props.node
  // 读侧归一（#1258 codex 四轮 P1）：本组件直连 parse（绕过 record/ghost
  // 映射），inputs: foo 等异形必须先归一，否则 formatLines 对字符串调
  // .join() 渲染期崩溃。
  const draftInputs = normalizeStringList(draft.inputs)
  const draftOutputs = normalizeStringList(draft.outputs)
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
          value={formatLines(draftInputs)}
          onChange={(event) => patchLines(patchWorkflowNodeInputs, event)}
          rows={Math.max(2, draftInputs.length)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输出产物，每行一个</span>
        <textarea
          aria-label="输出产物，每行一个"
          className={styles.fieldInput}
          value={formatLines(draftOutputs)}
          onChange={(event) => patchLines(patchWorkflowNodeOutputs, event)}
          rows={Math.max(2, draftOutputs.length)}
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
