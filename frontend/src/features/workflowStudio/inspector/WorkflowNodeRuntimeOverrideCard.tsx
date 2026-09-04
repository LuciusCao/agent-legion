import { useSettingStore } from '../../../stores/settingStore'
import { NodeConfigCard } from '../../../components/settings/NodeConfigCard'
import type { WorkflowNodeRecord } from '../../../types'
import styles from './WorkflowStructuredEditor.module.css'

// code 节点配置值的运行时覆盖通道（#418 后半）：live 设置卡片
// （NodeConfigCard，settings/nodes PATCH），UI 明示「立即生效、不产生
// 新版本」。从 WorkflowNodeConfigSection 拆出以守单文件预算。
export function WorkflowNodeRuntimeOverrideCard({
  node,
  readOnly,
}: {
  node: WorkflowNodeRecord
  readOnly?: boolean
}) {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const liveSchema = useSettingStore(
    (s) => s.settings.nodeConfigSchemas?.[node.key]
  )
  const initialValues = useSettingStore(
    (s) => s.settings.nodeConfig?.[node.key]
  )
  if (!workspaceId || !liveSchema) return null
  return readOnly ? (
    <p className={styles.fieldHint}>
      历史版本查看模式下运行时覆盖不可编辑；覆盖不属于 revision，
      请切回草稿视图修改。
    </p>
  ) : (
    <>
      <p className={styles.fieldHint}>
        运行时覆盖：立即保存到 workspace 设置，不产生新版本。非运行
        开关键影响之后 intake 的新 job；运行开关键（runtime_mutable） 对运行中的
        job 下一次执行即生效。
      </p>
      <NodeConfigCard
        key={node.key}
        workspaceId={workspaceId}
        nodeKey={node.key}
        label={node.label}
        schema={liveSchema}
        initialValues={initialValues ?? {}}
      />
    </>
  )
}
