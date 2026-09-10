/**
 * question 实体的预览 section 清单（issue #11 第 2 层：声明式 manifest）。
 *
 * 之前六个卡片的结构与 gating 硬编码在组件 JSX 里；现在集中为数据声明，
 * 后续 capability 键控 / 后端下发 manifest 只需替换本文件。
 *
 * gate 语义：status: 'completed' = 产出完成才可见（生成类节点）；
 * 'terminal' = 终态（completed/failed）即可见（评审失败也算「已尝试」）。
 */
import type { JobDetail, JobNode } from '../../types/jobTypes'

export type QuestionSectionId =
  | 'stem'
  | 'keyInfo'
  | 'options'
  | 'answer'
  | 'possibleErrors'
  | 'analysis'

export type QuestionSectionGateStatus = 'completed' | 'terminal'

export interface QuestionSectionGate {
  nodeKey: string
  status: QuestionSectionGateStatus
}

export interface QuestionSectionSpec {
  id: QuestionSectionId
  /** 无 gate 的 section 始终渲染（内容为空时组件自渲染空态）。 */
  gate?: QuestionSectionGate
}

const TERMINAL_NODE_STATUSES = new Set(['completed', 'failed'])

/** question 面板的 section 顺序与 gating 声明（渲染顺序即数组顺序）。 */
export const QUESTION_PREVIEW_SECTIONS: readonly QuestionSectionSpec[] = [
  { id: 'stem' },
  // prettier-ignore
  { id: 'keyInfo', gate: { nodeKey: 'generate_key_info', status: 'completed' } },
  { id: 'options' },
  { id: 'answer' },
  // prettier-ignore
  { id: 'possibleErrors', gate: { nodeKey: 'generate_possible_errors', status: 'completed' } },
  { id: 'analysis' },
] as const

/** 节点状态是否满足 gate（terminal：completed/failed 皆可）。 */
function gateMatches(gate: QuestionSectionGate, node: JobNode): boolean {
  if (node.node_key !== gate.nodeKey) return false
  return gate.status === 'terminal'
    ? TERMINAL_NODE_STATUSES.has(node.status)
    : node.status === 'completed'
}

/** 评审类 gate 的 node_key（terminal 即视为「已尝试」，报告要拉取）。 */
const REVIEW_GATE_NODE_KEYS = {
  keyInfo: 'review_key_info',
  possibleErrors: 'review_possible_errors',
} as const

export type QuestionReviewKind = keyof typeof REVIEW_GATE_NODE_KEYS

/** 某类评审是否已尝试（终态）：决定 review report 查询是否启用。 */
export function evaluateReviewAttempted(
  detail: JobDetail | null,
  kind: QuestionReviewKind
): boolean {
  const nodeKey = REVIEW_GATE_NODE_KEYS[kind]
  return (detail?.nodes ?? []).some(
    (node) =>
      node.node_key === nodeKey && TERMINAL_NODE_STATUSES.has(node.status)
  )
}

/**
 * 结构化面板消费的产物名集合（#255 方案 B）：通用产物预览据此默认隐藏
 * 同名卡片（勾选菜单可恢复）。名单必须与 questionPanel.html 的取数清单
 * 一致——questionPreviewManifest.test.ts 做 bundle 源码全等校验。
 */
export const QUESTION_CONSUMED_ARTIFACTS: readonly string[] = [
  'questions.json',
  'comprehension_info.json',
  'key_info_reviewed.json',
  'key_info_raw.json',
  'possible_errors_reviewed.json',
  'possible_errors_raw.json',
  'key_info_review_report.json',
  'possible_errors_review_report.json',
]

export type QuestionGateMap = Record<QuestionSectionId, boolean>

/** 求 section gating：无 gate 恒可见，有 gate 按节点状态判定。 */
export function evaluateQuestionGates(
  detail: JobDetail | null
): QuestionGateMap {
  const nodes = detail?.nodes ?? []
  const gates = {} as QuestionGateMap
  for (const section of QUESTION_PREVIEW_SECTIONS) {
    gates[section.id] = section.gate
      ? nodes.some((node) => gateMatches(section.gate!, node))
      : true
  }
  return gates
}
