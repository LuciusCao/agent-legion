import type { JobDetail } from '../types/jobTypes'

// Artifact 版本号：从共享的 jobDetail 查询数据 derive，编进 artifact 查询的
// queryKey。节点状态推进 → 版本变 → 新 key → 自动重取且不留旧数据，
// 语义等价于旧的 refreshKey + resetOnRun。

/** questions.json 的版本：产出节点（outputs 含 questions.json）的状态三元组。 */
export function questionArtifactVersion(detail: JobDetail | null): string {
  const producer = detail?.nodes.find((node) =>
    node.outputs?.includes('questions.json')
  )
  return producer
    ? [producer.status, producer.started_at, producer.finished_at].join(':')
    : ''
}

/** 审题信息相关 artifact 的版本：assemble / 两个 review 节点的状态三元组。 */
export function comprehensionVersion(detail: JobDetail | null): string {
  const findNode = (key: string) =>
    detail?.nodes.find((node) => node.node_key === key)
  const assembleNode = findNode('assemble_items')
  const reviewKeyInfoNode = findNode('review_key_info')
  const reviewPossibleErrorsNode = findNode('review_possible_errors')
  return [
    assembleNode?.status,
    assembleNode?.started_at,
    assembleNode?.finished_at,
    reviewKeyInfoNode?.status,
    reviewKeyInfoNode?.started_at,
    reviewKeyInfoNode?.finished_at,
    reviewPossibleErrorsNode?.status,
    reviewPossibleErrorsNode?.started_at,
    reviewPossibleErrorsNode?.finished_at,
  ].join(':')
}
