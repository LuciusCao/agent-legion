import { describe, it, expect } from 'vitest'
import type { JobNodeSummary } from '../../../jobTypes'
import { createJobSummary } from '../actions/testHelpers'
import {
  createOptionAccumulator,
  applyPatchToAccumulator,
  applyAppendToAccumulator,
} from './optionAccumulator'

function makeNode(key: string): JobNodeSummary {
  return {
    node_key: key,
    label: key.toUpperCase(),
    status: 'pending',
    error_message: '',
  }
}

describe('optionAccumulator', () => {
  it('creates counts from initial jobs', () => {
    const acc = createOptionAccumulator([
      createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        node_summaries: [makeNode('b')],
        workflow_version: 3,
      }),
      createJobSummary({
        id: 'j2',
        active_node_key: 'a',
        node_summaries: [makeNode('c')],
        workflow_version: 2,
      }),
      createJobSummary({
        id: 'j3',
        active_node_key: null,
        workflow_version: null,
      }),
    ])

    expect(acc.nodeKeyCounts.get('a')).toBe(2)
    expect(acc.nodeKeyCounts.get('b')).toBe(1)
    expect(acc.nodeKeyCounts.get('c')).toBe(1)
    expect(acc.workflowVersionCounts.get(3)).toBe(1)
    expect(acc.workflowVersionCounts.get(2)).toBe(1)
    expect(acc.missingWorkflowVersionCount).toBe(1)
    expect(acc.nodeKeys).toEqual(new Set(['a', 'b', 'c']))
    expect(acc.workflowVersionOptions.versionOptions).toEqual([3, 2])
    expect(acc.workflowVersionOptions.hasMissingVersion).toBe(true)
  })

  it('updates counts when a job changes node and version', () => {
    const acc = createOptionAccumulator([
      createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        workflow_version: 1,
      }),
    ])

    const jobsById = {
      j1: createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        workflow_version: 1,
      }),
    }

    applyPatchToAccumulator(
      acc,
      jobsById,
      [
        createJobSummary({
          id: 'j1',
          active_node_key: 'b',
          workflow_version: 2,
        }),
      ],
      []
    )

    expect(acc.nodeKeyCounts.has('a')).toBe(false)
    expect(acc.nodeKeyCounts.get('b')).toBe(1)
    expect(acc.workflowVersionCounts.has(1)).toBe(false)
    expect(acc.workflowVersionCounts.get(2)).toBe(1)
  })

  it('deletes a job contribution and removes keys with zero count', () => {
    const acc = createOptionAccumulator([
      createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        node_summaries: [makeNode('b')],
        workflow_version: 1,
      }),
      createJobSummary({
        id: 'j2',
        active_node_key: 'a',
        workflow_version: 1,
      }),
    ])

    const jobsById = {
      j1: createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        node_summaries: [makeNode('b')],
        workflow_version: 1,
      }),
      j2: createJobSummary({
        id: 'j2',
        active_node_key: 'a',
        workflow_version: 1,
      }),
    }

    applyPatchToAccumulator(acc, jobsById, [], ['j1'])

    expect(acc.nodeKeyCounts.get('a')).toBe(1)
    expect(acc.nodeKeyCounts.has('b')).toBe(false)
    expect(acc.workflowVersionCounts.get(1)).toBe(1)
  })

  it('appends jobs and increments counts', () => {
    const acc = createOptionAccumulator([
      createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        workflow_version: 1,
      }),
    ])
    const originalNodeKeys = acc.nodeKeys
    const originalVersionOptions = acc.workflowVersionOptions

    applyAppendToAccumulator(acc, [
      createJobSummary({
        id: 'j2',
        active_node_key: 'b',
        workflow_version: 2,
      }),
    ])

    expect(acc.nodeKeyCounts.get('a')).toBe(1)
    expect(acc.nodeKeyCounts.get('b')).toBe(1)
    expect(acc.workflowVersionCounts.get(1)).toBe(1)
    expect(acc.workflowVersionCounts.get(2)).toBe(1)
    expect(acc.nodeKeys).toEqual(new Set(['a', 'b']))
    expect(acc.nodeKeys).not.toBe(originalNodeKeys)
    expect(acc.workflowVersionOptions).not.toBe(originalVersionOptions)
    expect(acc.workflowVersionOptions.versionOptions).toEqual([2, 1])
  })

  it('keeps stable references when counts do not change', () => {
    const acc = createOptionAccumulator([
      createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        workflow_version: 1,
      }),
      createJobSummary({
        id: 'j2',
        active_node_key: 'b',
        workflow_version: 2,
      }),
    ])

    const jobsById = {
      j1: createJobSummary({
        id: 'j1',
        active_node_key: 'a',
        workflow_version: 1,
      }),
      j2: createJobSummary({
        id: 'j2',
        active_node_key: 'b',
        workflow_version: 2,
      }),
    }

    const nodeKeysBefore = acc.nodeKeys
    const versionOptionsBefore = acc.workflowVersionOptions

    applyPatchToAccumulator(
      acc,
      jobsById,
      [
        createJobSummary({
          id: 'j1',
          active_node_key: 'a',
          status: 'running',
          workflow_version: 1,
        }),
      ],
      []
    )

    expect(acc.nodeKeys).toBe(nodeKeysBefore)
    expect(acc.workflowVersionOptions).toBe(versionOptionsBefore)
    expect(acc.nodeKeyCounts.get('a')).toBe(1)
  })

  it('tracks missing workflow version across updates and deletes', () => {
    const acc = createOptionAccumulator([
      createJobSummary({ id: 'j1', workflow_version: null }),
      createJobSummary({ id: 'j2', workflow_version: null }),
    ])
    expect(acc.missingWorkflowVersionCount).toBe(2)
    expect(acc.workflowVersionOptions.hasMissingVersion).toBe(true)

    const jobsById = {
      j1: createJobSummary({ id: 'j1', workflow_version: null }),
      j2: createJobSummary({ id: 'j2', workflow_version: null }),
    }

    applyPatchToAccumulator(
      acc,
      jobsById,
      [createJobSummary({ id: 'j1', workflow_version: 5 })],
      []
    )
    expect(acc.missingWorkflowVersionCount).toBe(1)
    expect(acc.workflowVersionCounts.get(5)).toBe(1)

    applyPatchToAccumulator(acc, jobsById, [], ['j2'])
    expect(acc.missingWorkflowVersionCount).toBe(0)
    expect(acc.workflowVersionOptions.hasMissingVersion).toBe(false)
  })
})
