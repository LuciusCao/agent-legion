import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

// approval 节点专属 config（EXEC-APPROVAL-001）：与后端
// approval_node.py 的 _ALLOWED_CONFIG_KEYS 白名单一一对应——
// rework_target：rework 决策默认重置的上游节点（空 = 不预选）；
// feedback_artifact：评审备注写入的产物文件名（裸文件名，默认
// review_feedback.json）。写路径只落白名单键，其他键交给 YAML 编辑器。

export type ApprovalNodeConfig = {
  reworkTarget: string
  feedbackArtifact: string
}

export function readApprovalNodeConfig(
  rawYaml: string,
  nodeKey: string
): ApprovalNodeConfig {
  const node = parseWorkflowYaml(rawYaml).nodes?.[nodeKey]
  return {
    reworkTarget: node?.config?.rework_target ?? '',
    feedbackArtifact: node?.config?.feedback_artifact ?? 'review_feedback.json',
  }
}

// 覆写 approval config 白名单键；两个键都为空时整体删除 config（loader
// 对空 mapping 不报错，但干净草稿不携带空占位）。非 approval 节点拒绝
// 写入（白名单键只对 approval 有意义，前端 fail-closed）。
export function patchWorkflowNodeApprovalConfig(
  rawYaml: string,
  nodeKey: string,
  config: ApprovalNodeConfig
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node: WorkflowYamlNode | undefined = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (node.type !== 'approval') {
    throw new Error(`Node ${nodeKey} is not an approval node`)
  }
  const next: NonNullable<WorkflowYamlNode['config']> = {}
  if (config.reworkTarget) next.rework_target = config.reworkTarget
  if (config.feedbackArtifact) {
    // 后端校验：裸文件名（不得含路径分隔符）。
    if (/[/\\]/.test(config.feedbackArtifact)) {
      throw new Error('feedback_artifact 必须是裸文件名（不含路径）')
    }
    next.feedback_artifact = config.feedbackArtifact
  }
  if (Object.keys(next).length === 0) delete node.config
  else node.config = next
  return dumpWorkflowYaml(draft)
}
