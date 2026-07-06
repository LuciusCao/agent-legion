import { describe, it, expect } from 'vitest'
import { deriveJobDetailPresentation } from './deriveJobDetailPresentation'
import type { JobDetailResponse } from '../../types'

function makeNode(
  nodeKey: string,
  status: string,
  overrides: Partial<JobDetailResponse['nodes'][number]> = {}
): JobDetailResponse['nodes'][number] {
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

function makeDetail(nodes: JobDetailResponse['nodes']): JobDetailResponse {
  return {
    job: {
      id: 'job1',
      workspace_id: 'question_comprehension',
      workflow_key: 'question_comprehension_info',
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

describe('deriveJobDetailPresentation', () => {
  it('returns previewable flags based on generate nodes', () => {
    const detail = makeDetail([
      makeNode('generate_key_info', 'completed'),
      makeNode('generate_possible_errors', 'completed'),
      makeNode('review_key_info', 'running'),
      makeNode('review_possible_errors', 'pending'),
      makeNode('assemble_comprehension_info', 'pending'),
    ])

    const result = deriveJobDetailPresentation(detail)

    expect(result.keyInfoPreviewable).toBe(true)
    expect(result.possibleErrorsPreviewable).toBe(true)
    expect(result.keyInfoReviewAttempted).toBe(false)
    expect(result.possibleErrorsReviewAttempted).toBe(false)
  })

  it('returns reviewed flags based on review nodes', () => {
    const detail = makeDetail([
      makeNode('generate_key_info', 'completed'),
      makeNode('generate_possible_errors', 'completed'),
      makeNode('review_key_info', 'completed'),
      makeNode('review_possible_errors', 'completed'),
      makeNode('assemble_comprehension_info', 'pending'),
    ])

    const result = deriveJobDetailPresentation(detail)

    expect(result.keyInfoPreviewable).toBe(true)
    expect(result.possibleErrorsPreviewable).toBe(true)
    expect(result.keyInfoReviewAttempted).toBe(true)
    expect(result.possibleErrorsReviewAttempted).toBe(true)
  })

  it('does not preview before generate completes', () => {
    const detail = makeDetail([
      makeNode('generate_key_info', 'running'),
      makeNode('generate_possible_errors', 'pending'),
      makeNode('review_key_info', 'completed'),
      makeNode('review_possible_errors', 'completed'),
      makeNode('assemble_comprehension_info', 'pending'),
    ])

    const result = deriveJobDetailPresentation(detail)

    expect(result.keyInfoPreviewable).toBe(false)
    expect(result.possibleErrorsPreviewable).toBe(false)
  })

  it('treats failed review nodes as attempted so reports are still fetched', () => {
    const detail = makeDetail([
      makeNode('generate_key_info', 'completed'),
      makeNode('generate_possible_errors', 'completed'),
      makeNode('review_key_info', 'failed'),
      makeNode('review_possible_errors', 'failed'),
      makeNode('assemble_comprehension_info', 'pending'),
    ])

    const result = deriveJobDetailPresentation(detail)

    expect(result.keyInfoReviewAttempted).toBe(true)
    expect(result.possibleErrorsReviewAttempted).toBe(true)
  })

  it('returns all flags false when detail is null', () => {
    const result = deriveJobDetailPresentation(null)

    expect(result.keyInfoPreviewable).toBe(false)
    expect(result.possibleErrorsPreviewable).toBe(false)
    expect(result.keyInfoReviewAttempted).toBe(false)
    expect(result.possibleErrorsReviewAttempted).toBe(false)
  })
})
