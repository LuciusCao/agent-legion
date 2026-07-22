import type { ExecutorDefinition } from '../../executorTypes'
import type { WorkflowNodeRecord } from '../../types'
import editorStyles from './components/WorkflowStructuredEditor.module.css'
import styles from './WorkflowNodeRuntimeSettings.module.css'
import { patchWorkflowNodeExecution } from './workflowStudioYamlDraft.execution'
import { parseWorkflowNode } from './workflowStudioYamlDraft.parse'
import { WorkflowRuntimeInheritedField } from './WorkflowRuntimeInheritedField'

type CapabilityDetail = NonNullable<
  ExecutorDefinition['capability_details']
>[number]

export function WorkflowNodeRuntimeSettings(props: {
  node: WorkflowNodeRecord
  defaults: CapabilityDetail
  definitionYaml: string
  setDefinitionYaml: (yaml: string) => void
  readOnly?: boolean
}) {
  const draft = parseWorkflowNode(props.definitionYaml, props.node.key)
  const execution = draft
    ? (draft.execution ?? {})
    : (props.node.execution ?? {})
  const patch = (
    field: 'provider' | 'model' | 'thinking' | 'prompt',
    value: string
  ) =>
    props.setDefinitionYaml(
      patchWorkflowNodeExecution(
        props.definitionYaml,
        props.node.key,
        field,
        value
      )
    )
  return (
    <div className={styles.fields}>
      {props.node.max_concurrency != null && (
        <div
          className={editorStyles.fieldHint}
          data-testid="agent-node-summary"
          style={{ marginBottom: 4 }}
        >
          Agent 节点 · capability {props.node.capability}
          （并发上限为 workspace 级，在设置页配置）
        </div>
      )}
      <WorkflowRuntimeInheritedField
        label="Provider"
        value={execution.provider ?? ''}
        inherited={props.defaults.provider ?? ''}
        readOnly={props.readOnly}
        onChange={(value) => patch('provider', value)}
      />
      <WorkflowRuntimeInheritedField
        label="Model"
        value={execution.model ?? ''}
        inherited={props.defaults.model ?? ''}
        readOnly={props.readOnly}
        onChange={(value) => patch('model', value)}
      />
      <label className={editorStyles.field}>
        <span className={editorStyles.fieldLabel}>Thinking</span>
        <select
          aria-label="Thinking"
          className={editorStyles.fieldInput}
          value={execution.thinking ?? ''}
          disabled={props.readOnly}
          onChange={(event) => patch('thinking', event.target.value)}
        >
          <option value="">
            继承全局（{props.defaults.thinking || '未配置'}）
          </option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </label>
      <label className={editorStyles.field}>
        <span className={editorStyles.fieldLabel}>节点补充指令</span>
        <textarea
          aria-label="节点补充指令"
          className={editorStyles.fieldInput}
          value={execution.prompt ?? ''}
          rows={3}
          disabled={props.readOnly}
          placeholder="可选"
          onChange={(event) => patch('prompt', event.target.value)}
        />
        <span className={editorStyles.fieldHint}>
          追加到系统生成的运行 Prompt 末尾
        </span>
      </label>
    </div>
  )
}
