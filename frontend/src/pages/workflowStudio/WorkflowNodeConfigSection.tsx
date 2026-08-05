import { NodeConfigCard } from '../../components/settings/NodeConfigCard'
import { useSettingStore } from '../../stores/settingStore'
import type { WorkflowNodeRecord } from '../../types'
import inspectorStyles from './WorkflowNodeInspector.module.css'

// Per-node config editing lives in the studio inspector (in context) instead
// of the settings page; the section renders only when the node declares a
// config schema (agent config_schema or executor capability fallback).
export function WorkflowNodeConfigSection(props: {
  node: WorkflowNodeRecord
  readOnly?: boolean
}) {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const schema = useSettingStore(
    (s) => s.settings.nodeConfigSchemas?.[props.node.key]
  )
  const initialValues = useSettingStore(
    (s) => s.settings.nodeConfig?.[props.node.key]
  )
  if (!workspaceId || !schema) return null
  return (
    <section
      className={inspectorStyles.section}
      aria-label={`节点配置 ${props.node.key}`}
    >
      <div className={inspectorStyles.sectionTitle}>节点配置</div>
      {props.readOnly && (
        <div className={inspectorStyles.empty}>
          当前为历史版本查看模式；配置不属于 revision，保存将修改当前 workspace
          设置。
        </div>
      )}
      <NodeConfigCard
        key={props.node.key}
        workspaceId={workspaceId}
        nodeKey={props.node.key}
        label={props.node.label}
        schema={schema}
        initialValues={initialValues ?? {}}
      />
    </section>
  )
}
