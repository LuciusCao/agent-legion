import { useMemo } from 'react'
import type {
  WorkflowNodeRecord,
  WorkspaceRuntimeModelsResponse,
} from '../../../types'
import styles from './WorkflowNodeRuntimeSettings.module.css'
import { useRuntimeModelOptions } from './runtimeModelOptions'
import { WorkflowNodeThinkingField } from './WorkflowNodeThinkingField'
import { patchWorkflowNodeExecution } from '../shared/workflowStudioYamlDraft.execution'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'
import type { WorkflowYamlExecutionDefaults } from '../shared/workflowStudioYamlDraft.executionDefaults'
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
  const patch = (field: 'provider' | 'model' | 'thinking', value: string) =>
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
      <WorkflowNodeThinkingField
        value={execution.thinking ?? ''}
        inherited={props.defaults.thinking ?? ''}
        readOnly={props.readOnly}
        onChange={(value) => patch('thinking', value)}
      />
      {/* execution.prompt（节点指令）的编辑统一在「查看 Prompt」预览面板
          （默认组装 + 整段替代 + 重置），此处不再重复提供文本框。 */}
    </div>
  )
}
