import { describe, expect, it, vi } from 'vitest'
import {
  conditionLabel,
  groupValidationErrors,
  isDefinitionDirty,
  selectedNodeDetails,
} from './workflowStudioModel'
import { parseCompareErrors } from './workflowStudioErrors'
import type { WorkflowDefinitionRecord } from '../../types'
import type { components } from '../../generated/api'

type CompareError = components['schemas']['WorkflowDraftCompareError']

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

  it('groups validation errors into yaml, schema, structure, executor, and revision buckets', () => {
    const grouped = groupValidationErrors([
      'Workflow nodes are required',
      'missing executor binding for question_comprehension_info.fetch_questions',
      'executor local is not allocated to workspace ws1',
      'executor pi does not support capability review_key_info',
      "YAML parse error: could not find expected ':'",
      'Schema validation failed: nodes field is required',
      'No active revision found',
    ])

    expect(grouped.yaml).toEqual([
      "YAML parse error: could not find expected ':'",
    ])
    expect(grouped.schema).toEqual([
      'Schema validation failed: nodes field is required',
    ])
    expect(grouped.structure).toEqual(['Workflow nodes are required'])
    expect(grouped.executor).toHaveLength(3)
    expect(grouped.revision).toEqual(['No active revision found'])
  })

  it('parses compare errors into scoped groups with callbacks', () => {
    const onSelectNode = vi.fn()
    const errors: CompareError[] = [
      {
        category: 'yaml',
        message: "could not find expected ':'",
        line: 18,
        column: 7,
      },
      {
        category: 'schema',
        message: 'Missing capability',
        node_key: 'classify',
      },
      {
        category: 'structure',
        message: 'Edge target missing',
        source: 'classify',
        target: 'missing',
      },
    ]

    const groups = parseCompareErrors(errors, onSelectNode)

    expect(groups.map((group) => group.categoryLabel)).toEqual([
      'YAML解析',
      '结构校验',
      '结构',
    ])
    const schemaGroup = groups.find((group) => group.category === 'schema')!
    schemaGroup.items[0].onSelectNode?.()
    expect(onSelectNode).toHaveBeenCalledWith('classify')

    const structureGroup = groups.find(
      (group) => group.category === 'structure'
    )!
    expect(structureGroup.items[0].source).toBe('classify')
    expect(structureGroup.items[0].target).toBe('missing')
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
