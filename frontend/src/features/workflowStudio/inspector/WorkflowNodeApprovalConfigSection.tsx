import type { WorkflowNodeRecord } from '../../../types'
import {
  patchWorkflowNodeApprovalConfig,
  readApprovalNodeConfig,
} from '../shared/workflowStudioYamlDraft.approvalConfig'
import { approvalReworkCandidates } from '../shared/workflowStudioApprovalRework'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import editorStyles from './WorkflowStructuredEditor.module.css'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// 审批门专属配置（#392 Phase 2，EXEC-APPROVAL-001）：rework_target =
// rework 决策默认重置的上游节点（候选 = 祖先闭包内的可执行节点，见
// workflowStudioApprovalRework）；feedback_artifact = 评审备注写入的
// 产物文件名。写路径经 patchWorkflowNodeApprovalConfig 只落白名单键。
export function WorkflowNodeApprovalConfigSection(props: Props) {
  const config = readApprovalNodeConfig(props.definitionYaml, props.node.key)
  const candidates = approvalReworkCandidates(
    props.definitionYaml,
    props.node.key
  )
  const patch = (next: Partial<typeof config>) => {
    try {
      props.setDefinitionYaml(
        patchWorkflowNodeApprovalConfig(props.definitionYaml, props.node.key, {
          ...config,
          ...next,
        })
      )
    } catch {
      // 非法输入（如 feedback_artifact 带路径）不落草稿；静默保留原值，
      // 输入框受控回弹。本节点只可能是 approval（registry 保证）。
    }
  }
  return (
    <section className={inspectorStyles.section} aria-label="审批门配置">
      <div className={inspectorStyles.sectionTitle}>审批门配置</div>
      <label className={editorStyles.field}>
        <span className={editorStyles.fieldLabel}>重置目标（rework）</span>
        <select
          aria-label="重置目标"
          className={editorStyles.fieldInput}
          value={config.reworkTarget}
          disabled={props.readOnly}
          onChange={(event) => patch({ reworkTarget: event.target.value })}
        >
          <option value="">（rework 决策时选择）</option>
          {candidates.map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </select>
      </label>
      <label className={editorStyles.field}>
        <span className={editorStyles.fieldLabel}>评审备注文件名</span>
        <input
          aria-label="评审备注文件名"
          className={editorStyles.fieldInput}
          value={config.feedbackArtifact}
          disabled={props.readOnly}
          onChange={(event) => patch({ feedbackArtifact: event.target.value })}
        />
      </label>
      <p className={editorStyles.fieldHint}>
        审批门不派发执行：就绪后停在 awaiting_approval 等人工决策；rework
        把「重置目标」节点连同其下游重置，评审备注写入上述产物文件。
      </p>
    </section>
  )
}
