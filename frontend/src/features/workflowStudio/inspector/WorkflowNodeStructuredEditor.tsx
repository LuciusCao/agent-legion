import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowChannelBadge } from './WorkflowChannelBadge'
import { useStudioStateOptional } from '../shared/studioStateContext'
import {
  parseWorkflowNode,
  patchWorkflowNodeCapability,
  patchWorkflowNodeLabel,
} from '../shared/workflowStudioYamlDraft'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  onDefinitionYamlChange: (nextYaml: string) => void
}

export function WorkflowNodeStructuredEditor({
  node,
  definitionYaml,
  onDefinitionYamlChange,
}: Props) {
  const draftNode = parseWorkflowNode(definitionYaml, node.key) ?? node
  const label = draftNode.label
  const capability = draftNode.capability ?? node.capability
  // M48：结构字段的「已修改 · 待发布」标记（原型屏 3/4）——compare 报告的
  // 该节点变更字段即数据源；execution-only 变更已被单独归类，不会误挂。
  const studio = useStudioStateOptional()
  const dirtyFields = new Set(
    studio?.compareSummary?.nodeChanges.find(
      (item) => item.nodeKey === node.key
    )?.fields ?? []
  )
  const handleLabelChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeLabel(definitionYaml, node.key, event.target.value)
    )
  const handleCapabilityChange = (event: React.ChangeEvent<HTMLInputElement>) =>
    onDefinitionYamlChange(
      patchWorkflowNodeCapability(definitionYaml, node.key, event.target.value)
    )
  // approval 契约禁 capability（loader _FORBIDDEN_APPROVAL_FIELDS），基本
  // 设置不给能力 Key 输入；start 不走本编辑器（契约段独占）。
  const showsCapability = node.node_type !== 'approval'

  return (
    <section
      aria-label="Workflow node structured editor"
      className={styles.structuredSection}
    >
      <h3 className={styles.structuredTitle}>
        基本设置
        <WorkflowChannelBadge kind="structure" />
      </h3>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>
          节点名称
          {dirtyFields.has('label') && (
            <span className={styles.dirtyDot}>已修改 · 待发布</span>
          )}
        </span>
        <input
          aria-label="节点名称"
          className={styles.fieldInput}
          value={label}
          onChange={handleLabelChange}
        />
      </label>
      {showsCapability && (
        <label className={styles.field}>
          <span className={styles.fieldLabel}>
            能力 Key
            {dirtyFields.has('capability') && (
              <span className={styles.dirtyDot}>已修改 · 待发布</span>
            )}
          </span>
          <input
            aria-label="能力"
            className={styles.fieldInput}
            value={capability}
            onChange={handleCapabilityChange}
          />
        </label>
      )}
    </section>
  )
}
