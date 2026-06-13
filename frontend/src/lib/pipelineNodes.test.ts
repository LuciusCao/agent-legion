import { describe, it, expect } from 'vitest'
import {
  nodesForJob,
  computeOrderedNodes,
  excludedJobs,
  type PipelineNodesByKey,
} from './pipelineNodes'
import type { JobSummary, PipelineDefinitionRecord } from '../types'

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 'j1',
    workspace_id: 'ws1',
    pipeline_key: 'p1',
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

const pipeline: PipelineDefinitionRecord = {
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

const otherPipeline: PipelineDefinitionRecord = {
  key: 'other_pipeline',
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

const pipelineNodesByKey: PipelineNodesByKey = {
  question_content: pipeline,
  other_pipeline: otherPipeline,
}

describe('pipelineNodes', () => {
  describe('nodesForJob', () => {
    it('returns nodes from pipelineNodesByKey when available', () => {
      const job = makeJob({ pipeline_key: 'question_content' })
      expect(nodesForJob(job, pipelineNodesByKey, null)).toEqual(pipeline.nodes)
    })

    it('falls back to pipelineDefinition when key matches', () => {
      const job = makeJob({ pipeline_key: 'question_content' })
      expect(nodesForJob(job, null, pipeline)).toEqual(pipeline.nodes)
    })

    it('prefers pipelineNodesByKey over pipelineDefinition', () => {
      const job = makeJob({ pipeline_key: 'question_content' })
      expect(nodesForJob(job, pipelineNodesByKey, otherPipeline)).toEqual(
        pipeline.nodes
      )
    })

    it('returns null when pipeline is unknown', () => {
      const job = makeJob({ pipeline_key: 'unknown' })
      expect(nodesForJob(job, pipelineNodesByKey, pipeline)).toBeNull()
    })
  })

  describe('computeOrderedNodes', () => {
    it('returns an empty array when no jobs are provided', () => {
      expect(computeOrderedNodes([], pipeline, null)).toEqual([])
    })

    it('returns all nodes for a single known pipeline', () => {
      const jobs = [makeJob({ pipeline_key: 'question_content' })]
      expect(computeOrderedNodes(jobs, pipeline, null)).toEqual(pipeline.nodes)
    })

    it('returns nodes from pipelineNodesByKey when definition is omitted', () => {
      const jobs = [makeJob({ pipeline_key: 'question_content' })]
      expect(computeOrderedNodes(jobs, null, pipelineNodesByKey)).toEqual(
        pipeline.nodes
      )
    })

    it('returns an empty array when no job has a known pipeline', () => {
      const jobs = [makeJob({ pipeline_key: 'unknown' })]
      expect(computeOrderedNodes(jobs, pipeline, pipelineNodesByKey)).toEqual(
        []
      )
    })

    it('returns the intersection of common nodes across multiple pipelines', () => {
      const jobs = [
        makeJob({ id: 'j1', pipeline_key: 'question_content' }),
        makeJob({ id: 'j2', pipeline_key: 'other_pipeline' }),
      ]
      const result = computeOrderedNodes(jobs, pipeline, pipelineNodesByKey)
      expect(result).toHaveLength(1)
      expect(result[0].key).toBe('extract')
    })

    it('preserves the order of the first job’s nodes', () => {
      const jobs = [
        makeJob({ id: 'j1', pipeline_key: 'question_content' }),
        makeJob({ id: 'j2', pipeline_key: 'other_pipeline' }),
      ]
      const result = computeOrderedNodes(jobs, pipeline, pipelineNodesByKey)
      expect(result.map((n) => n.key)).toEqual(['extract'])
    })
  })

  describe('excludedJobs', () => {
    it('returns jobs whose pipeline does not contain the node key', () => {
      const jobs = [
        makeJob({
          id: 'j1',
          pipeline_key: 'question_content',
          source_id: 'Q1',
        }),
        makeJob({ id: 'j2', pipeline_key: 'other_pipeline', source_id: 'Q2' }),
      ]
      const result = excludedJobs(
        jobs,
        'generate',
        pipelineNodesByKey,
        pipeline
      )
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe('j2')
    })

    it('returns all jobs when the node key is unknown', () => {
      const jobs = [
        makeJob({ id: 'j1', pipeline_key: 'question_content' }),
        makeJob({ id: 'j2', pipeline_key: 'other_pipeline' }),
      ]
      const result = excludedJobs(jobs, 'unknown', pipelineNodesByKey, pipeline)
      expect(result).toHaveLength(2)
    })

    it('returns no jobs when every job contains the node key', () => {
      const jobs = [
        makeJob({ id: 'j1', pipeline_key: 'question_content' }),
        makeJob({ id: 'j2', pipeline_key: 'other_pipeline' }),
      ]
      const result = excludedJobs(jobs, 'extract', pipelineNodesByKey, pipeline)
      expect(result).toHaveLength(0)
    })

    it('excludes jobs with unknown pipelines', () => {
      const jobs = [
        makeJob({ id: 'j1', pipeline_key: 'question_content' }),
        makeJob({ id: 'j2', pipeline_key: 'unknown' }),
      ]
      const result = excludedJobs(jobs, 'extract', pipelineNodesByKey, pipeline)
      expect(result).toHaveLength(1)
      expect(result[0].id).toBe('j2')
    })
  })
})
