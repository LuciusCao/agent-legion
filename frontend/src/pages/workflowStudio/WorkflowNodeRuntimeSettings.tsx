import { useMemo } from 'react'
import type {
  WorkflowNodeRecord,
  WorkspaceRuntimeModelsResponse,
} from '../../types'
import editorStyles from './components/WorkflowStructuredEditor.module.css'
import styles from './WorkflowNodeRuntimeSettings.module.css'
import { useRuntimeModelOptions } from './runtimeModelOptions'
import { patchWorkflowNodeExecution } from './workflowStudioYamlDraft.execution'
import { parseWorkflowNode } from './workflowStudioYamlDraft.parse'
import type { WorkflowYamlExecutionDefaults } from './workflowStudioYamlDraft.parse'
import { WorkflowRuntimeInheritedField } from './WorkflowRuntimeInheritedField'

export function WorkflowNodeRuntimeSettings(props: {
  node: WorkflowNodeRecord
  runtime: string
  defaults: WorkflowYamlExecutionDefaults
  runtimeModels?: WorkspaceRuntimeModelsResponse['runtimes']
  definitionYaml: string
  setDefinitionYaml: (yaml: string) => void
  readOnly?: boolean
}) {
  // 全量 YAML parse 按草稿内容 + 节点 key memo，不随每次渲染重算。
  const draft = useMemo(
    () => parseWorkflowNode(props.definitionYaml, props.node.key),
    [props.definitionYaml, props.node.key]
  )
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

  const { providerOptions, modelOptions } = useRuntimeModelOptions(
    props.runtimeModels,
    props.runtime,
    execution.provider ?? ''
  )
  const datalistPrefix = `runtime-options-${props.node.key}`

  return (
    <div className={styles.fields}>
      <WorkflowRuntimeInheritedField
        label="Provider"
        value={execution.provider ?? ''}
        inherited={props.defaults.provider ?? ''}
        options={providerOptions}
        listId={`${datalistPrefix}-provider`}
        readOnly={props.readOnly}
        onChange={(value) => patch('provider', value)}
      />
      <WorkflowRuntimeInheritedField
        label="Model"
        value={execution.model ?? ''}
        inherited={props.defaults.model ?? ''}
        options={modelOptions}
        listId={`${datalistPrefix}-model`}
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
            {props.defaults.thinking
              ? `继承 workflow 默认（${props.defaults.thinking}）`
              : 'runtime 决定'}
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
