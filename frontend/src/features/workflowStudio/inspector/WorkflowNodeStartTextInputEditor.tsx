import type { WorkflowNodeRecord } from '../../../types'
import {
  EMPTY_TEXT_INPUT,
  patchWorkflowNodeTextInput,
  type WorkflowTextInputDraft,
} from '../shared/workflowStudioYamlDraft.textInput'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
}

/** 「直接输入需求」的呈现配置编辑器（start 节点勾选 text 后出现）：输入框
 * 标题、落盘文件名、预填模板，patch 回 draft YAML 的 text_input 块。 */
export function WorkflowNodeStartTextInputEditor(props: Props) {
  const current: WorkflowTextInputDraft = {
    ...EMPTY_TEXT_INPUT,
    ...(props.node.text_input ?? {}),
  }
  const patch = (field: keyof WorkflowTextInputDraft, value: string) =>
    props.setDefinitionYaml(
      patchWorkflowNodeTextInput(props.definitionYaml, props.node.key, {
        ...current,
        [field]: value,
      })
    )
  return (
    <div className={styles.fieldGroup} data-testid="start-text-input-editor">
      <div className={styles.fieldHint}>
        「直接输入需求」的呈现方式：这些内容会出现在「添加条目 · 输入需求」里。
      </div>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>输入框标题</span>
        <input
          aria-label="输入框标题"
          className={styles.fieldInput}
          placeholder="需求内容"
          value={current.label}
          onChange={(event) => patch('label', event.target.value)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>落盘文件名（.md 或 .txt）</span>
        <input
          aria-label="落盘文件名"
          className={styles.fieldInput}
          placeholder="需求.md"
          value={current.filename}
          onChange={(event) => patch('filename', event.target.value)}
        />
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>预填模板</span>
        <textarea
          aria-label="预填模板"
          className={styles.fieldInput}
          placeholder="用户打开「输入需求」时预填的内容，按自己的方向修改后才能提交"
          value={current.template}
          onChange={(event) => patch('template', event.target.value)}
          rows={Math.min(16, Math.max(4, current.template.split('\n').length))}
        />
      </label>
    </div>
  )
}
