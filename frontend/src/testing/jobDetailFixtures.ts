// 拆出 fixtures.ts 以过架构文件预算；panel/manifest 测试共用。
import type { JobDetail } from '../types/jobTypes'
import { makeJob } from './fixtures'

/** question 面板 gating 所需的最小 jobDetail（manifest 求值用 nodes）。 */
export function makeJobDetail(
  nodes: Array<Partial<JobDetail['nodes'][number]>> = [],
  overrides: Partial<JobDetail> = {}
): JobDetail {
  return {
    job: makeJob(),
    nodes: nodes.map((node, idx) => ({
      id: idx + 1,
      job_id: 'j1',
      node_key: node.node_key ?? `node_${idx + 1}`,
      label: node.label ?? node.node_key ?? `node_${idx + 1}`,
      status: node.status ?? 'completed',
      capability: node.capability ?? node.node_key ?? `node_${idx + 1}`,
      created_at: node.created_at ?? '',
      after: node.after ?? [],
      inputs: node.inputs ?? [],
      outputs: node.outputs ?? [],
      error_message: node.error_message ?? '',
      stale_reason: node.stale_reason ?? '',
      executor_kind: node.executor_kind ?? 'code',
      ...node,
    })),
    runs: [],
    artifacts: overrides.artifacts ?? [],
    ...overrides,
  }
}
