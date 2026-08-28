/**
 * question 实体的预览 section 清单（issue #11 第 2 层：声明式 manifest）。
 *
 * 之前六个卡片的结构与 gating（node_key 字符串 + 状态判断）硬编码在
 * QuestionContentPanel 的 JSX 与 deriveJobDetailPresentation 里；现在集中
 * 为数据声明。后续 capability 键控 / 后端下发 manifest 只需替换本文件，
 * section 渲染组件与面板框架不再感知业务键名。
 *
 * gate 语义：
 * - status: 'completed'  —— 产出完成才可见（生成类节点）；
 * - status: 'terminal'   —— 终态（completed/failed）即可见（评审类节点：
 *   评审失败也算「已尝试」，报告要展示）。
 */
import type { JobDetail } from '../../types/jobTypes'
import type { JobNode } from '../../types/jobTypes'

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
  { id: 'keyInfo', gate: { nodeKey: 'generate_key_info', status: 'completed' } },
  { id: 'options' },
  { id: 'answer' },
  {
    id: 'possibleErrors',
    gate: { nodeKey: 'generate_possible_errors', status: 'completed' },
  },
  { id: 'analysis' },
] as const

function gateVisible(gate: QuestionSectionGate, nodes: JobNode[]): boolean {
  return nodes.some((node) => {
    if (node.node_key !== gate.nodeKey) return false
    if (gate.status === 'terminal') return TERMINAL_NODE_STATUSES.has(node.status)
    return node.status === 'completed'
  })
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

export type QuestionGateMap = Record<QuestionSectionId, boolean>

/** 求 section gating：无 gate 恒可见，有 gate 按节点状态判定。 */
export function evaluateQuestionGates(detail: JobDetail | null): QuestionGateMap {
  const nodes = detail?.nodes ?? []
  const gates = {} as QuestionGateMap
  for (const section of QUESTION_PREVIEW_SECTIONS) {
    gates[section.id] = section.gate ? gateVisible(section.gate, nodes) : true
  }
  return gates
}
