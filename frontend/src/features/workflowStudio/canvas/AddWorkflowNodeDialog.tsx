import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useUiStore } from '../../../stores/uiStore'
import type { SwitchableNodeType } from '../shared/workflowStudioYamlDraft.nodeType'
import {
  appendWorkflowNode,
  WorkflowNodeAppendError,
} from '../shared/workflowStudioYamlDraft.appendNode'
import styles from './AddWorkflowNodeDialog.module.css'

// 类型标签本地副本：与 inspector 选择器（Phase 1 侧 NodeTypeSelect 的
// TYPE_LABELS）同一文案；Phase 2 的 nodeTypeSections 合入后统一。
const NODE_TYPE_LABELS: Record<SwitchableNodeType, string> = {
  code: 'Code',
  agent: 'Agent',
  approval: '审批门',
}

type Props = {
  open: boolean
  definitionYaml: string
  readOnly?: boolean
  onClose: () => void
  /** 追加成功：返回改写后的草稿 YAML 与新节点 key，由调用侧落草稿并
   * 选中新节点。 */
  onAppended: (yaml: string, nodeKey: string) => void
}

// 画布「添加节点」对话框（#392 Phase 3）：类型三选一 + key（label 与
// capability 缺省 = key，approval 无 capability）。提交经
// appendWorkflowNode 全量校验后追加进草稿并选中新节点；新节点默认
// 不接线——提示文案说明接线方式（approval 需可执行入边，validate 会
// 引导）。失败 toast 原因、草稿不动（AGENTS.md L88）。open 切换时整体
// 卸载重挂：每次打开都是全新表单状态，无需手动清理上次输入。
export function AddWorkflowNodeDialog(props: Props) {
  if (!props.open) return null
  return <AddWorkflowNodeForm {...props} />
}

function AddWorkflowNodeForm(props: Props) {
  const showToast = useUiStore((s) => s.showToast)
  const [nodeType, setNodeType] = useState<SwitchableNodeType>('code')
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [capability, setCapability] = useState('')

  function submit() {
    const nodeKey = key.trim()
    try {
      const nextYaml = appendWorkflowNode(props.definitionYaml, {
        nodeType,
        key,
        label: label || undefined,
        capability: capability || undefined,
      })
      props.onAppended(nextYaml, nodeKey)
      showToast(`节点「${nodeKey}」已添加（未接线）`, 'success')
      props.onClose()
    } catch (error) {
      showToast(
        error instanceof WorkflowNodeAppendError
          ? error.message
          : '添加节点失败；请手动在 YAML 追加',
        'error'
      )
    }
  }

  return (
    <Dialog open onClose={props.onClose} fullWidth maxWidth="sm">
      <DialogTitle>添加节点</DialogTitle>
      <DialogContent className={styles.body}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>类型</span>
          <select
            aria-label="节点类型"
            className={styles.fieldInput}
            value={nodeType}
            disabled={props.readOnly}
            onChange={(event) =>
              setNodeType(event.target.value as SwitchableNodeType)
            }
          >
            {(Object.keys(NODE_TYPE_LABELS) as SwitchableNodeType[]).map(
              (type) => (
                <option key={type} value={type}>
                  {NODE_TYPE_LABELS[type]}
                </option>
              )
            )}
          </select>
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>节点 Key</span>
          <input
            aria-label="节点 Key"
            className={styles.fieldInput}
            value={key}
            disabled={props.readOnly}
            onChange={(event) => setKey(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>节点名称（缺省 = Key）</span>
          <input
            aria-label="节点名称"
            className={styles.fieldInput}
            value={label}
            disabled={props.readOnly}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        {nodeType !== 'approval' && (
          <label className={styles.field}>
            <span className={styles.fieldLabel}>
              能力 Key（缺省 = 节点 Key）
            </span>
            <input
              aria-label="能力 Key"
              className={styles.fieldInput}
              value={capability}
              disabled={props.readOnly}
              onChange={(event) => setCapability(event.target.value)}
            />
          </label>
        )}
        <p className={styles.hint}>
          新节点默认不接线：请用「编辑 YAML」在 nodes 段给新节点补
          after/edges（节点详情的「依赖关系」是只读展示）；审批门需要
          至少一条来自可执行节点的入边才能通过校验。
        </p>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={props.onClose}>
          取消
        </Button>
        <Button variant="contained" disabled={props.readOnly} onClick={submit}>
          添加
        </Button>
      </DialogActions>
    </Dialog>
  )
}
