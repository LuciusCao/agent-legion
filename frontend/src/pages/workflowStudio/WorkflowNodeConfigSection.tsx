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
      {props.readOnly ? (
        // 历史版本查看模式：配置不属于 revision，但保存会直接写 live 设置
        // ——只读视图锁死（与 code 段的 writable = !readOnly 对齐）。
        <div className={inspectorStyles.empty}>
          历史版本查看模式下节点配置不可编辑；配置不属于
          revision，请切回草稿视图修改。
        </div>
      ) : (
        <NodeConfigCard
          key={props.node.key}
          workspaceId={workspaceId}
          nodeKey={props.node.key}
          label={props.node.label}
          schema={schema}
          initialValues={initialValues ?? {}}
        />
      )}
    </section>
  )
}
