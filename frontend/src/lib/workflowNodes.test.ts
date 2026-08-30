import { describe, it, expect } from 'vitest'
import {
  nodesForJob,
  computeOrderedNodes,
  excludedJobs,
  type WorkflowNodesByKey,
} from './workflowNodes'
import { partitionJobsForNodeRerun } from '../components/JobRerunDialog/rerunEligibility'
import type { JobSummary, WorkflowDefinitionRecord } from '../types'

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  const job = {
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
    workflow_revision_id: '',
    workflow_version: null,
    workflow_definition_hash: '',
    outcome: '',
    current_workflow_revision_id: '',
    current_workflow_revision_version: null,
    is_workflow_outdated: false,
    packed: 0,
    ...overrides,
  }
  return {
    ...job,
    is_workflow_outdated: job.is_workflow_outdated ?? false,
  }
}

const workflow: WorkflowDefinitionRecord = {
  key: 'question_content',
  label: 'Question Content',
  intake: { modes: [] },
  edges: [],
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
  edges: [],
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

// Definitions parsed after the start-node feature always carry a synthetic
// `_start` entry node that never executes and never appears in job_nodes.
const workflowWithStart: WorkflowDefinitionRecord = {
  ...workflow,
  nodes: [
    {
      key: '_start',
      label: '入口',
      after: [],
      capability: '',
      inputs: [] as string[],
      outputs: [] as string[],
      node_type: 'start',
    },
    ...workflow.nodes,
  ],
}

describe('workflowNodes', () => {
  describe('nodesForJob', () => {
    it('returns nodes from workflowNodesByKey when available', () => {
      const job = makeJob({ workspace_id: 'question_content' })
      expect(nodesForJob(job, workflowNodesByKey, null)).toEqual(workflow.nodes)
    })

    it('falls back to workflowDefinition when key matches', () => {
      const job = makeJob({ workspace_id: 'question_content' })
      expect(nodesForJob(job, null, workflow)).toEqual(workflow.nodes)
    })

    it('prefers workflowNodesByKey over workflowDefinition', () => {
      const job = makeJob({ workspace_id: 'question_content' })
      expect(nodesForJob(job, workflowNodesByKey, otherWorkflow)).toEqual(
        workflow.nodes
      )
    })

    it('returns null when workflow is unknown', () => {
      const job = makeJob({ workspace_id: 'unknown' })
      expect(nodesForJob(job, workflowNodesByKey, workflow)).toBeNull()
    })

    it('filters out start nodes', () => {
      const job = makeJob({ workspace_id: 'question_content' })
      const nodes = nodesForJob(job, null, workflowWithStart)
      expect(nodes).toEqual(workflow.nodes)
      expect(nodes?.some((n) => n.key === '_start')).toBe(false)
    })
  })

  describe('computeOrderedNodes', () => {
    it('returns an empty array when no jobs are provided', () => {
      expect(computeOrderedNodes([], workflow, null)).toEqual([])
    })

    it('returns all nodes for a single known workflow', () => {
      const jobs = [makeJob({ workspace_id: 'question_content' })]
      expect(computeOrderedNodes(jobs, workflow, null)).toEqual(workflow.nodes)
    })

    it('returns nodes from workflowNodesByKey when definition is omitted', () => {
      const jobs = [makeJob({ workspace_id: 'question_content' })]
      expect(computeOrderedNodes(jobs, null, workflowNodesByKey)).toEqual(
        workflow.nodes
      )
    })

    it('returns an empty array when no job has a known workflow', () => {
      const jobs = [makeJob({ workspace_id: 'unknown' })]
      expect(computeOrderedNodes(jobs, workflow, workflowNodesByKey)).toEqual(
        []
      )
    })

    it('returns the intersection of common nodes across multiple workflows', () => {
      const jobs = [
        makeJob({ id: 'j1', workspace_id: 'question_content' }),
        makeJob({ id: 'j2', workspace_id: 'other_workflow' }),
      ]
      const result = computeOrderedNodes(jobs, workflow, workflowNodesByKey)
      expect(result).toHaveLength(1)
      expect(result[0].key).toBe('extract')
    })

    it('preserves the order of the first job’s nodes', () => {
      const jobs = [
        makeJob({ id: 'j1', workspace_id: 'question_content' }),
        makeJob({ id: 'j2', workspace_id: 'other_workflow' }),
      ]
      const result = computeOrderedNodes(jobs, workflow, workflowNodesByKey)
      expect(result.map((n) => n.key)).toEqual(['extract'])
    })

    it('excludes start nodes so rerun/run-to dialogs default to an executable node', () => {
      const jobs = [makeJob({ workspace_id: 'question_content' })]
      const result = computeOrderedNodes(jobs, workflowWithStart, null)
      expect(result.map((n) => n.key)).toEqual([
        'extract',
        'generate',
        'review',
      ])
      expect(result[0].key).not.toBe('_start')
    })

    it('keeps rerun grouping intact when the definition contains a start node', () => {
      const jobs = [
        makeJob({
          id: 'j1',
          workspace_id: 'question_content',
          node_summaries: [
            {
              node_key: 'extract',
              label: '提取',
              status: 'completed',
              error_message: '',
            },
          ],
        }),
        makeJob({
          id: 'j2',
          workspace_id: 'question_content',
          node_summaries: [],
        }),
      ]
      const excluded = excludedJobs(jobs, 'extract', null, workflowWithStart)
      expect(excluded).toHaveLength(0)
      const { runnableJobs, notStartedJobs, runningJobs } =
        partitionJobsForNodeRerun(jobs, 'extract', excluded)
      expect(runnableJobs.map((j) => j.id)).toEqual(['j1'])
      expect(notStartedJobs.map((j) => j.id)).toEqual(['j2'])
      expect(runningJobs).toHaveLength(0)
    })
  })

  describe('excludedJobs', () => {
    it('returns jobs whose workflow does not contain the node key', () => {
      const jobs = [
        makeJob({
          id: 'j1',
          workspace_id: 'question_content',
          source_id: 'Q1',
        }),
        makeJob({ id: 'j2', workspace_id: 'other_workflow', source_id: 'Q2' }),
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
        makeJob({ id: 'j1', workspace_id: 'question_content' }),
        makeJob({ id: 'j2', workspace_id: 'other_workflow' }),
      ]
      const result = excludedJobs(jobs, 'unknown', workflowNodesByKey, workflow)
      expect(result).toHaveLength(2)
    })

    it('returns no jobs when every job contains the node key', () => {
      const jobs = [
        makeJob({ id: 'j1', workspace_id: 'question_content' }),
        makeJob({ id: 'j2', workspace_id: 'other_workflow' }),
      ]
      const result = excludedJobs(jobs, 'extract', workflowNodesByKey, workflow)
      expect(result).toHaveLength(0)
    })

    it('excludes jobs with unknown workflows', () => {
      const jobs = [
        makeJob({ id: 'j1', workspace_id: 'question_content' }),
        makeJob({ id: 'j2', workspace_id: 'unknown' }),
      ]
      const result = excludedJobs(jobs, 'extract', workflowNodesByKey, workflow)
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe('j2')
    })
  })
})
