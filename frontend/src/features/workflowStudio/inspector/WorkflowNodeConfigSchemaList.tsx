import type { ConfigSchema, WorkflowNodeRecord } from '../../../types'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft'
import {
  removeWorkflowNodeSchemaProperty,
  renameWorkflowNodeSchemaProperty,
} from '../shared/workflowStudioYamlDraft.configSchema.properties'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import { WorkflowNodeConfigSchemaProperties } from './WorkflowNodeConfigSchemaProperties'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// config_schema 属性列表的编排（#418 面板）：解析草稿 schema、过滤
// YAML 中间态的 null 属性、挂接改名/删除的 YAML 写回。从
// WorkflowNodeConfigSchemaSection 拆出以守单文件预算。
export function WorkflowNodeConfigSchemaList({
  node,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  const schema: ConfigSchema | undefined = parseWorkflowNode(
    definitionYaml,
    node.key
  )?.config_schema
  const properties = schema?.properties ?? {}
  const keys = Object.keys(properties).filter(
    (propKey) =>
      properties[propKey] != null && typeof properties[propKey] === 'object'
  )
  // 写路径失败（非法输入）不落草稿；受控输入回弹。
  const apply = (next: string) => {
    try {
      setDefinitionYaml(next)
    } catch {
      /* 回弹 */
    }
  }
  if (keys.length === 0) {
    return (
      <div className={inspectorStyles.empty}>
        该节点未声明 config_schema；可在下方新增第一个属性，或用 YAML
        源码编辑器为节点添加。
      </div>
    )
  }
  return (
    <WorkflowNodeConfigSchemaProperties
      properties={properties}
      propKeys={keys}
      readOnly={readOnly}
      onRename={(propKey, nextName) =>
        apply(
          renameWorkflowNodeSchemaProperty(
            definitionYaml,
            node.key,
            propKey,
            nextName
          )
        )
      }
      onRemove={(propKey) =>
        apply(
          removeWorkflowNodeSchemaProperty(definitionYaml, node.key, propKey)
        )
      }
      definitionYaml={definitionYaml}
      nodeKey={node.key}
      setDefinitionYaml={setDefinitionYaml}
    />
  )
}
