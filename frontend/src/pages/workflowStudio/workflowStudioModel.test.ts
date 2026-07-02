import { describe, expect, it } from 'vitest'
import {
  conditionLabel,
  groupValidationErrors,
  isDefinitionDirty,
  selectedNodeDetails,
} from './workflowStudioModel'
import type { WorkflowDefinitionRecord } from '../../types'

const workflow: WorkflowDefinitionRecord = {
  key: 'question_comprehension_info',
  label: '题目审题信息生成 DAG',
  intake: { modes: [] },
  nodes: [
    {
      key: 'classify',
      label: '判断是否适合审题',
      capability: 'classify_comprehension_eligibility',
      after: [],
      inputs: ['questions_parsed.json'],
      outputs: ['comprehension_eligibility.json'],
    },
    {
      key: 'assemble',
      label: '组装审题信息',
      capability: 'assemble_comprehension_info',
      after: [],
      inputs: ['comprehension_eligibility.json'],
      outputs: ['manifest.json'],
    },
  ],
  edges: [
    {
      source: 'classify',
      target: 'assemble',
      condition: {
        artifact: 'comprehension_eligibility.json',
        path: '$.eligible',
        equals: true,
      },
    },
  ],
}

describe('workflowStudioModel', () => {
  it('formats condition labels', () => {
    expect(conditionLabel(workflow.edges[0].condition)).toBe(
      '$.eligible == true'
    )
    expect(conditionLabel(null)).toBe('')
  })

  it('groups validation errors into structural and executor binding buckets', () => {
    const grouped = groupValidationErrors([
      'Workflow nodes are required',
      'missing executor binding for question_comprehension_info.fetch_questions',
      'executor local is not allocated to workspace ws1',
      'executor pi does not support capability review_key_info',
    ])

    expect(grouped.structural).toEqual(['Workflow nodes are required'])
    expect(grouped.executor).toHaveLength(3)
  })

  it('detects dirty definition source after trimming only line endings', () => {
    expect(isDefinitionDirty('key: demo\n', 'key: demo\n')).toBe(false)
    expect(isDefinitionDirty('key: demo\n', 'key: changed\n')).toBe(true)
  })

  it('returns selected node details with incoming and outgoing edges', () => {
    const details = selectedNodeDetails(workflow, 'classify')

    expect(details?.node.key).toBe('classify')
    expect(details?.incoming).toEqual([])
    expect(details?.outgoing).toHaveLength(1)
  })
})
