import type { WorkflowNodeRecord } from '../../../types'
import {
  ITEM_TYPE_DISPLAY,
  type AcceptedItemType,
} from '../../../lib/acceptedItemTypes'
import { patchWorkflowNodeAcceptedItemTypes } from '../workflowStudioYamlDraft'
import styles from './WorkflowStructuredEditor.module.css'

// 规范写回顺序 = ITEM_TYPE_DISPLAY 的 key 顺序（material/ref/bundle）。
const ITEM_TYPE_ORDER = Object.keys(ITEM_TYPE_DISPLAY) as AcceptedItemType[]

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
}

/** start 节点入口契约编辑器：勾选这个工作流接受哪些内容作为输入，patch 回
 * draft YAML（修改仍走既有 draft→publish 流）。至少保留一项——唯一已选项的
 * checkbox 置灰防空集。选项文案统一走 ITEM_TYPE_DISPLAY（与「添加条目」
 * 对话框、readOnly 视图同源）。 */
export function WorkflowNodeStartContractEditor(props: Props) {
  const selected = props.node.accepted_item_types ?? []
  const toggle = (value: string, checked: boolean) => {
    // 固定按规范顺序写回，与勾选顺序无关。
    const next = ITEM_TYPE_ORDER.filter((v) =>
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
        这个工作流接受哪些内容作为输入。这里的选择决定「添加条目」对话框里提供哪些提交方式。
      </div>
      <div className={styles.fieldHint}>
        勾选「外部平台内容」前，需要管理员先配置外部服务连接。
      </div>
      {ITEM_TYPE_ORDER.map((value) => {
        const display = ITEM_TYPE_DISPLAY[value]
        return (
          <label key={value} className={styles.itemTypeOption}>
            <input
              type="checkbox"
              checked={selected.includes(value)}
              disabled={selected.length === 1 && selected[0] === value}
              onChange={(event) => toggle(value, event.target.checked)}
            />
            <span>{display.label}</span>
            <span className={styles.fieldHint}>{display.description}</span>
          </label>
        )
      })}
    </div>
  )
}
