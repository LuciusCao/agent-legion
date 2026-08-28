import type { JobDetail } from '../types/jobTypes'

// Artifact 版本号：从共享的 jobDetail 查询数据 derive，编进 artifact 查询的
// queryKey。节点状态推进 → 版本变 → 新 key → 自动重取且不留旧数据，
// 语义等价于旧的 refreshKey + resetOnRun。

/** 节点状态三元组：status/started_at/finished_at 拼接。 */
function nodeTriple(
  node: JobDetail['nodes'][number] | undefined
): string | null {
  return node
    ? [node.status, node.started_at, node.finished_at].join(':')
    : null
}

/** questions.json 的版本：产出节点（outputs 含 questions.json）的状态三元组。 */
export function questionArtifactVersion(detail: JobDetail | null): string {
  return (
    nodeTriple(
      detail?.nodes.find((node) => node.outputs?.includes('questions.json'))
    ) ?? ''
  )
}

/** 审题信息相关 artifact 的版本：assemble / 两个 review 节点的状态三元组。 */
export function comprehensionVersion(detail: JobDetail | null): string {
  const triples = [
    'assemble_items',
    'review_key_info',
    'review_possible_errors',
  ].map((key) =>
    nodeTriple(detail?.nodes.find((node) => node.node_key === key))
  )
  return triples.flat().join(':')
}

/**
 * 通用预览面板的 artifact 版本：优先产出节点（outputs 含该 artifact 名）
 * 的状态三元组；无产出节点声明时回退 job 状态（job 重跑仍会失效缓存）。
 */
export function artifactVersion(
  detail: JobDetail | null,
  artifactName: string
): string {
  const producer = nodeTriple(
    detail?.nodes.find((node) => node.outputs?.includes(artifactName))
  )
  if (producer) return producer
  const job = detail?.job
  return job ? [job.status, job.created_at ?? '', job.id].join(':') : ''
}
