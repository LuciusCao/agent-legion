import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

// approval 节点专属 config（EXEC-APPROVAL-001）：与后端
// approval_node.py 的 _ALLOWED_CONFIG_KEYS 白名单一一对应——
// rework_target：rework 决策默认重置的上游节点（空 = 不预选）；
// feedback_artifact：评审备注写入的产物文件名（裸文件名，默认
// review_feedback.json）。写路径只落白名单键；approval 节点上其余
// config 键本就 loader 非法，覆写时一并丢弃（fail-closed）。

export type ApprovalNodeConfig = {
  reworkTarget: string
  feedbackArtifact: string
}

// 读侧在渲染路径被调用：YAML 编辑中途的非法文本（未闭合括号等）会让
// parse 抛错——此时返回默认值而不是拖垮整个 Studio（仓库纪律：
// workflowYamlDraftRecord 同款；编辑合法后自然恢复真实值）。config 的
// YAML 类型是自由 mapping（code 节点同键承载任意 schema 参数值，#418），
// 这里按白名单键读字符串。
function readStr(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback
}

export function readApprovalNodeConfig(
  rawYaml: string,
  nodeKey: string
): ApprovalNodeConfig {
  try {
    const config = parseWorkflowYaml(rawYaml).nodes?.[nodeKey]?.config
    const feedback = readStr(config?.feedback_artifact, 'review_feedback.json')
    return {
      reworkTarget: readStr(config?.rework_target, ''),
      feedbackArtifact: feedback,
    }
  } catch {
    return { reworkTarget: '', feedbackArtifact: 'review_feedback.json' }
  }
}

// 覆写 approval config 白名单键；两个键都为空时整体删除 config（loader
// 对空 mapping 不报错，但干净草稿不携带空占位）。**只覆写传入的键**——
// 未传的键保留草稿现值，UI 只改一个键时不会把读侧默认值物化进草稿。
// 非 approval 节点拒绝写入（白名单键只对 approval 有意义，前端
// fail-closed）；写侧在校验全部通过前不触碰草稿（AGENTS.md L88）。
export function patchWorkflowNodeApprovalConfig(
  rawYaml: string,
  nodeKey: string,
  config: Partial<ApprovalNodeConfig>
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node: WorkflowYamlNode | undefined = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (node.type !== 'approval') {
    throw new Error(`Node ${nodeKey} is not an approval node`)
  }
  if (config.feedbackArtifact && /[/\\]/.test(config.feedbackArtifact)) {
    // 后端校验：裸文件名（不得含路径分隔符）。
    throw new Error('feedback_artifact 必须是裸文件名（不含路径）')
  }
  const pick = (next: string | undefined, key: string): string =>
    next ?? readStr(node.config?.[key], '')
  const next: Record<string, string> = {}
  const reworkTarget = pick(config.reworkTarget, 'rework_target')
  const feedbackArtifact = pick(config.feedbackArtifact, 'feedback_artifact')
  if (reworkTarget) next.rework_target = reworkTarget
  if (feedbackArtifact) next.feedback_artifact = feedbackArtifact
  if (Object.keys(next).length === 0) delete node.config
  else node.config = next
  return dumpWorkflowYaml(draft)
}
