import { describe, it, expect } from 'vitest'
import {
  evaluateQuestionGates,
  evaluateReviewAttempted,
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
