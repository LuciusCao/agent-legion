import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  evaluateQuestionGates,
  evaluateReviewAttempted,
  QUESTION_CONSUMED_ARTIFACTS,
  QUESTION_PREVIEW_SECTIONS,
} from './questionPreviewManifest'
import type { JobDetail } from '../../types/jobTypes'

function makeNode(nodeKey: string, status: string): JobDetail['nodes'][number] {
  return {
    id: 1,
    job_id: 'j1',
    node_key: nodeKey,
    label: nodeKey,
    status,
    capability: nodeKey,
    created_at: '',
    after: [],
    inputs: [],
    outputs: [],
    error_message: '',
    stale_reason: '',
    executor_kind: 'code',
  }
}

function makeDetail(nodes: JobDetail['nodes']): JobDetail {
  return {
    job: {
      id: 'j1',
      workspace_id: 'ws1',
      workflow_key: 'demo',
      source_id: 'Q1',
      source_type: 'question',
      title: '',
      status: 'completed',
      batch_id: 'b1',
      created_at: '',
      updated_at: '',
      storage_dir: '',
      error_message: '',
      error_summary: '',
      completed_nodes: nodes.length,
      total_nodes: nodes.length,
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

describe('questionPreviewManifest', () => {
  it('section 顺序即声明顺序（渲染顺序由 manifest 决定）', () => {
    expect(QUESTION_PREVIEW_SECTIONS.map((section) => section.id)).toEqual([
      'stem',
      'keyInfo',
      'options',
      'answer',
      'possibleErrors',
      'analysis',
    ])
  })

  it('生成类 gate 只认 completed', () => {
    const gates = evaluateQuestionGates(
      makeDetail([
        makeNode('generate_key_info', 'completed'),
        makeNode('generate_possible_errors', 'running'),
      ])
    )

    expect(gates.keyInfo).toBe(true)
    expect(gates.possibleErrors).toBe(false)
  })

  it('评审类 gate 视 failed 为已尝试（报告仍要拉取）', () => {
    const detail = makeDetail([
      makeNode('review_key_info', 'failed'),
      makeNode('review_possible_errors', 'completed'),
    ])

    expect(evaluateReviewAttempted(detail, 'keyInfo')).toBe(true)
    expect(evaluateReviewAttempted(detail, 'possibleErrors')).toBe(true)

    const gates = evaluateQuestionGates(detail)
    expect(gates.keyInfo).toBe(false)
    expect(gates.possibleErrors).toBe(false)
  })

  it('running 评审不算已尝试', () => {
    const detail = makeDetail([
      makeNode('review_key_info', 'running'),
      makeNode('review_possible_errors', 'pending'),
    ])

    expect(evaluateReviewAttempted(detail, 'keyInfo')).toBe(false)
    expect(evaluateReviewAttempted(detail, 'possibleErrors')).toBe(false)
  })

  it('无 gate 的 section 恒可见', () => {
    const gates = evaluateQuestionGates(makeDetail([]))

    expect(gates.stem).toBe(true)
    expect(gates.options).toBe(true)
    expect(gates.answer).toBe(true)
    expect(gates.analysis).toBe(true)
  })

  it('detail 为 null 时 gate 全关（面板等待 detail）', () => {
    const gates = evaluateQuestionGates(null)

    expect(gates.keyInfo).toBe(false)
    expect(gates.possibleErrors).toBe(false)
    expect(evaluateReviewAttempted(null, 'keyInfo')).toBe(false)
    expect(evaluateReviewAttempted(null, 'possibleErrors')).toBe(false)
  })
})

describe('QUESTION_CONSUMED_ARTIFACTS（结构化面板消费产物，#255）', () => {
  // 名单从 questionPanel.html 的取数代码静态提取（readArtifact 调用点 +
  // 回落链数组），与 QUESTION_CONSUMED_ARTIFACTS 全等——bundle 改数据源
  // 而名单没跟上时，通用面板会漏去重（原始 JSON 重复占屏）。
  const bundleSource = readFileSync(
    join(
      import.meta.dirname,
      '../../features/previewPanel/builtin/questionPanel.html'
    ),
    'utf8'
  )
  const bundleArtifactNames = new Set<string>()
  for (const m of bundleSource.matchAll(
    /readJsonArtifactOrNull\('([^']+)'\)/g
  )) {
    bundleArtifactNames.add(m[1])
  }
  // 回落链数组（firstNonNull(['reviewed.json', 'raw.json'])）。
  for (const m of bundleSource.matchAll(/firstNonNull\(\[([^\]]+)\]/g)) {
    for (const name of m[1].matchAll(/'([^']+)'/g))
      bundleArtifactNames.add(name[1])
  }

  it('与内置 bundle 实际读取的产物名集合全等（防漂移）', () => {
    const declared = new Set(QUESTION_CONSUMED_ARTIFACTS)
    expect([...declared].sort()).toEqual([...bundleArtifactNames].sort())
  })
})
