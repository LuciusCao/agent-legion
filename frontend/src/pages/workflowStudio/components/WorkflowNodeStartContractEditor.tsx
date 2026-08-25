import type { WorkflowNodeRecord } from '../../../types'
import { patchWorkflowNodeAcceptedItemTypes } from '../workflowStudioYamlDraft'
import styles from './WorkflowStructuredEditor.module.css'

// 入口契约可选项：material/ref/bundle 的非空子集（EXEC-WORKFLOW-START-001）。
const ITEM_TYPES = [
  { value: 'material', label: '材料文件 material' },
  { value: 'ref', label: '外部引用 ref' },
  { value: 'bundle', label: '文件夹 bundle' },
] as const

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
}

/** start 节点入口契约编辑器：勾选接受的条目类型，patch 回 draft YAML（修改仍走
 * 既有 draft→publish 流）。至少保留一项——唯一已选项的 checkbox 置灰防空集。 */
export function WorkflowNodeStartContractEditor(props: Props) {
  const selected = props.node.accepted_item_types ?? []
  const toggle = (value: string, checked: boolean) => {
    // 固定按 material/ref/bundle 规范顺序写回，与勾选顺序无关。
    const next = ITEM_TYPES.map((t) => t.value).filter((v) =>
      v === value ? checked : selected.includes(v)
    )
    if (next.length === 0) return
    props.setDefinitionYaml(
      patchWorkflowNodeAcceptedItemTypes(
        props.definitionYaml,
        props.node.key,
        next
      )
    )
  }
  return (
    <div className={styles.fieldGroup}>
      <div className={styles.fieldHint}>
        该节点是 workflow 入口（type: start），永不执行；勾选接受的条目类型：
      </div>
      {ITEM_TYPES.map(({ value, label }) => (
        <label key={value}>
          <input
            type="checkbox"
            checked={selected.includes(value)}
            disabled={selected.length === 1 && selected[0] === value}
            onChange={(event) => toggle(value, event.target.checked)}
          />
          {label}
        </label>
      ))}
    </div>
  )
}
