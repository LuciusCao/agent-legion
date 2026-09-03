import type { ConfigSchemaProperty } from '../../../types'
import { patchWorkflowNodeSchemaProperty } from '../shared/workflowStudioYamlDraft.configSchema.properties'
import type { SchemaPropertyPatch } from '../shared/workflowStudioYamlDraft.configSchema.properties'
import styles from './WorkflowStructuredEditor.module.css'
import { WorkflowNodeSchemaPropertyRow } from './WorkflowNodeSchemaPropertyRow'

type Props = {
  properties: Record<string, ConfigSchemaProperty>
  propKeys: string[]
  readOnly?: boolean
  onRename: (propKey: string, nextName: string) => void
  onRemove: (propKey: string) => void
  definitionYaml: string
  nodeKey: string
  setDefinitionYaml: (value: string) => void
}

// 节点 config_schema 的属性列表（#418 后半）：每属性一行完整可编辑
// （行编辑器在 WorkflowNodeSchemaPropertyRow）。patch 直写草稿，失败
// 静默回弹。从 WorkflowNodeConfigSchemaSection 拆出以守单文件预算。
export function WorkflowNodeConfigSchemaProperties({
  properties,
  propKeys,
  readOnly,
  onRename,
  onRemove,
  definitionYaml,
  nodeKey,
  setDefinitionYaml,
}: Props) {
  const patch = (propKey: string, changes: SchemaPropertyPatch) => {
    try {
      setDefinitionYaml(
        patchWorkflowNodeSchemaProperty(
          definitionYaml,
          nodeKey,
          propKey,
          changes
        )
      )
    } catch {
      // 非法输入不落草稿；受控输入回弹。
    }
  }

  return (
    <div className={styles.fieldStack}>
      {propKeys.map((propKey) => (
        <WorkflowNodeSchemaPropertyRow
          key={propKey}
          propKey={propKey}
          prop={properties[propKey]}
          otherKeys={propKeys.filter((key) => key !== propKey)}
          readOnly={readOnly}
          onPatch={patch}
          onRename={onRename}
          onRemove={onRemove}
        />
      ))}
    </div>
  )
}
