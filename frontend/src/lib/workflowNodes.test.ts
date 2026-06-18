import { describe, it, expect } from 'vitest'
import {
  nodesForJob,
  computeOrderedNodes,
  excludedJobs,
  type WorkflowNodesByKey,
} from './workflowNodes'
import type { JobSummary, WorkflowDefinitionRecord } from '../types'

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 'j1',
    workspace_id: 'ws1',
    workflow_key: 'p1',
    source_id: 'Q1',
    source_type: 'question',
    title: '',
    status: 'pending',
    batch_id: 'b1',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    storage_dir: '/tmp/j1',
    error_message: '',
    error_summary: '',
    completed_nodes: 0,
    total_nodes: 0,
    ...overrides,
  }
}

const workflow: WorkflowDefinitionRecord = {
  key: 'question_content',
  label: 'Question Content',
  intake: { modes: [] },
  nodes: [
    {
      key: 'extract',
      label: '提取',
      after: [],
      capability: 'extract',
      inputs: [] as string[],
      outputs: [] as string[],
    },
    {
      key: 'generate',
      label: '生成',
      after: ['extract'],
      capability: 'generate',
      inputs: [] as string[],
      outputs: [] as string[],
    },
    {
      key: 'review',
      label: '审核',
      after: ['generate'],
      capability: 'review',
      inputs: [] as string[],
      outputs: [] as string[],
    },
  ],
}

const otherWorkflow: WorkflowDefinitionRecord = {
  key: 'other_workflow',
  label: 'Other',
  intake: { modes: [] },
  nodes: [
    {
      key: 'extract',
      label: '提取',
      after: [],
      capability: 'extract',
      inputs: [] as string[],
      outputs: [] as string[],
    },
    {
      key: 'convert',
      label: '转换',
      after: ['extract'],
      capability: 'convert',
      inputs: [] as string[],
      outputs: [] as string[],
    },
  ],
}

const workflowNodesByKey: WorkflowNodesByKey = {
  question_content: workflow,
  other_workflow: otherWorkflow,
}

describe('workflowNodes', () => {
  describe('nodesForJob', () => {
    it('returns nodes from workflowNodesByKey when available', () => {
      const job = makeJob({ workflow_key: 'question_content' })
      expect(nodesForJob(job, workflowNodesByKey, null)).toEqual(workflow.nodes)
    })

    it('falls back to workflowDefinition when key matches', () => {
      const job = makeJob({ workflow_key: 'question_content' })
      expect(nodesForJob(job, null, workflow)).toEqual(workflow.nodes)
    })

    it('prefers workflowNodesByKey over workflowDefinition', () => {
      const job = makeJob({ workflow_key: 'question_content' })
      expect(nodesForJob(job, workflowNodesByKey, otherWorkflow)).toEqual(
        workflow.nodes
      )
    })

    it('returns null when workflow is unknown', () => {
      const job = makeJob({ workflow_key: 'unknown' })
      expect(nodesForJob(job, workflowNodesByKey, workflow)).toBeNull()
    })
  })

  describe('computeOrderedNodes', () => {
    it('returns an empty array when no jobs are provided', () => {
      expect(computeOrderedNodes([], workflow, null)).toEqual([])
    })

    it('returns all nodes for a single known workflow', () => {
      const jobs = [makeJob({ workflow_key: 'question_content' })]
      expect(computeOrderedNodes(jobs, workflow, null)).toEqual(workflow.nodes)
    })

    it('returns nodes from workflowNodesByKey when definition is omitted', () => {
      const jobs = [makeJob({ workflow_key: 'question_content' })]
      expect(computeOrderedNodes(jobs, null, workflowNodesByKey)).toEqual(
        workflow.nodes
      )
    })

    it('returns an empty array when no job has a known workflow', () => {
      const jobs = [makeJob({ workflow_key: 'unknown' })]
      expect(computeOrderedNodes(jobs, workflow, workflowNodesByKey)).toEqual(
        []
      )
    })

    it('returns the intersection of common nodes across multiple workflows', () => {
      const jobs = [
        makeJob({ id: 'j1', workflow_key: 'question_content' }),
        makeJob({ id: 'j2', workflow_key: 'other_workflow' }),
      ]
      const result = computeOrderedNodes(jobs, workflow, workflowNodesByKey)
      expect(result).toHaveLength(1)
      expect(result[0].key).toBe('extract')
    })

    it('preserves the order of the first job’s nodes', () => {
      const jobs = [
        makeJob({ id: 'j1', workflow_key: 'question_content' }),
        makeJob({ id: 'j2', workflow_key: 'other_workflow' }),
      ]
      const result = computeOrderedNodes(jobs, workflow, workflowNodesByKey)
      expect(result.map((n) => n.key)).toEqual(['extract'])
    })
  })

  describe('excludedJobs', () => {
    it('returns jobs whose workflow does not contain the node key', () => {
      const jobs = [
        makeJob({
          id: 'j1',
          workflow_key: 'question_content',
          source_id: 'Q1',
        }),
        makeJob({ id: 'j2', workflow_key: 'other_workflow', source_id: 'Q2' }),
      ]
      const result = excludedJobs(
        jobs,
        'generate',
        workflowNodesByKey,
        workflow
      )
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe('j2')
    })

    it('returns all jobs when the node key is unknown', () => {
      const jobs = [
        makeJob({ id: 'j1', workflow_key: 'question_content' }),
        makeJob({ id: 'j2', workflow_key: 'other_workflow' }),
      ]
      const result = excludedJobs(jobs, 'unknown', workflowNodesByKey, workflow)
      expect(result).toHaveLength(2)
    })

    it('returns no jobs when every job contains the node key', () => {
      const jobs = [
        makeJob({ id: 'j1', workflow_key: 'question_content' }),
        makeJob({ id: 'j2', workflow_key: 'other_workflow' }),
      ]
      const result = excludedJobs(jobs, 'extract', workflowNodesByKey, workflow)
      expect(result).toHaveLength(0)
    })

    it('excludes jobs with unknown workflows', () => {
      const jobs = [
        makeJob({ id: 'j1', workflow_key: 'question_content' }),
        makeJob({ id: 'j2', workflow_key: 'unknown' }),
      ]
      const result = excludedJobs(jobs, 'extract', workflowNodesByKey, workflow)
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe('j2')
    })
  })
})
