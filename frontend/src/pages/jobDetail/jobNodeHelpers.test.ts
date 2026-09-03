import { describe, it, expect } from 'vitest'
import { toDagEdges, toDagNodes } from './jobNodeHelpers'
import { toNodeCatalog } from './deriveJobDetailPresentation'
import type { JobDetail } from '../../types/jobTypes'

function makeNode(
  nodeKey: string,
  status: string,
  overrides: Partial<JobDetail['nodes'][number]> = {}
): JobDetail['nodes'][number] {
  return {
    id: 1,
    job_id: 'job1',
    node_key: nodeKey,
    status,
    stale_reason: '',
    error_message: '',
    started_at: null,
    finished_at: null,
    created_at: '2026-06-29 10:00:00',
    label: nodeKey,
    capability: nodeKey,
    after: [],
    inputs: [],
    outputs: [],
    executor_id: null,
    executor_kind: null,
    ...overrides,
  }
}

function makeDetail(nodes: JobDetail['nodes']): JobDetail {
  return {
    job: {
      id: 'job1',
      // catalog key/label 现在读 workspace_id（workflow_key 已 deprecated 且
      // v62 起恒等，#211 Phase 2）——两者在此 fixture 中保持恒等值。
      workspace_id: 'demo_workflow',
      workflow_key: 'demo_workflow',
      source_type: 'question',
      source_id: 'q1',
      batch_id: 'batch1',
      title: 'Test',
      status: 'failed',
      storage_dir: '/tmp/job1',
      error_message: '',
      created_at: '2026-06-29 10:00:00',
      updated_at: '2026-06-29 10:00:00',
      node_summaries: [],
      completed_nodes: 0,
      total_nodes: nodes.length,
      active_node_key: null,
      error_summary: '',
      execution_control: {
        mode: 'full',
        target_node_key: null,
        paused: false,
        pause_reason: '',
      },
      workflow_revision_id: '',
      workflow_version: null,
      workflow_definition_hash: '',
      outcome: '',
      current_workflow_revision_id: '',
      current_workflow_revision_version: null,
      is_workflow_outdated: false,
      packed: 0,
    },
    nodes,
    runs: [],
    artifacts: [],
  }
}

describe('toNodeCatalog', () => {
  it('carries key, label, capability and the after edges for every node', () => {
    // Regression pin for fd74c5e1: dropping `after` silently broke run-to
    // start-node validation (ancestorClosure walks these edges).
    const detail = makeDetail([
      makeNode('generate_key_info', 'completed'),
      makeNode('review_key_info', 'running', {
        label: 'Review key info',
        capability: 'review_key_info',
        after: ['generate_key_info'],
      }),
      makeNode('assemble_items', 'pending', {
        after: ['generate_key_info', 'review_key_info'],
      }),
    ])

    const catalog = toNodeCatalog(detail)

    expect(catalog).toEqual({
      key: 'demo_workflow',
      label: 'demo_workflow',
      nodes: [
        {
          key: 'generate_key_info',
          label: 'generate_key_info',
          capability: 'generate_key_info',
          after: [],
        },
        {
          key: 'review_key_info',
          label: 'Review key info',
          capability: 'review_key_info',
          after: ['generate_key_info'],
        },
        {
          key: 'assemble_items',
          label: 'assemble_items',
          capability: 'assemble_items',
          after: ['generate_key_info', 'review_key_info'],
        },
      ],
    })
  })

  it('returns null when detail is null', () => {
    expect(toNodeCatalog(null)).toBeNull()
  })
})

describe('toDagNodes', () => {
  it('maps logical Agent and physical Worker separately onto dag nodes', () => {
    const nodes = toDagNodes([
      makeNode('generate', 'running', {
        executor_kind: 'pi',
        agent_id: 'key-info-generator',
        worker_id: 'worker-abc123',
      }),
    ])

    expect(nodes[0].executorKind).toBe('pi')
    expect(nodes[0].agentId).toBe('key-info-generator')
    expect(nodes[0].executorId).toBeNull()
    expect(nodes[0].workerId).toBe('worker-abc123')
  })

  it('defaults executor ids to null when absent', () => {
    const nodes = toDagNodes([makeNode('generate', 'pending')])

    expect(nodes[0].executorKind).toBeNull()
    expect(nodes[0].executorId).toBeNull()
    expect(nodes[0].agentId).toBeNull()
    expect(nodes[0].workerId).toBeNull()
  })
})

describe('toDagEdges (#417 job 详情边完整性)', () => {
  it('rebuilds the full DAG from a schema v2 multi-node job detail', () => {
    // 后端 job_nodes_with_definition → effective_after：schema v2 的边由
    // 顶层 edges 派生（_start 边隐藏）。该形状下 toDagEdges 必须产出完整
    // 拓扑链——缺一条边，DagGraph 就退化为无边图（#417 的直接症状）。
    const detail = makeDetail([
      makeNode('intake', 'completed', { after: [] }),
      makeNode('expand_analysis', 'completed', { after: ['intake'] }),
      makeNode('review_a', 'running', { after: ['expand_analysis'] }),
      makeNode('review_b', 'pending', { after: ['expand_analysis'] }),
      makeNode('publish', 'pending', {
        after: ['review_a', 'review_b'],
      }),
    ])

    const edges = toDagEdges(detail.nodes)

    expect(edges).toEqual([
      { from: 'intake', to: 'expand_analysis' },
      { from: 'expand_analysis', to: 'review_a' },
      { from: 'expand_analysis', to: 'review_b' },
      { from: 'review_a', to: 'publish' },
      { from: 'review_b', to: 'publish' },
    ])
  })

  it('derives an edgeless graph when the backend sends empty after lists', () => {
    // 后端侧已由 tests/services/test_job_query_service.py 的 schema v2
    // 回归钉锁死（effective_after 派生完整链路）；这里钉住前端语义：
    // after 全空时 toDagEdges 老实返回空数组（配合 DagGraph 的无边
    // 网格布局 + 提示，而不是渲染假边）。
    const detail = makeDetail([
      makeNode('intake', 'completed'),
      makeNode('expand_analysis', 'pending'),
    ])

    expect(toDagEdges(detail.nodes)).toEqual([])
  })
})
