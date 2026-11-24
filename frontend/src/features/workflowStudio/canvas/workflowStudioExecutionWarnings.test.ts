import { describe, expect, it } from 'vitest'
import { nodeExecutionWarning } from './workflowStudioExecutionWarnings'
import type { WorkflowNodeRecord } from '../../../types'

function makeNode(
  key: string,
  nodeType: string,
  execution?: { provider?: string; model?: string; thinking?: string }
): WorkflowNodeRecord {
  return {
    key,
    label: key,
    capability: 'cap',
    after: [],
    inputs: [],
    outputs: [],
    node_type: nodeType,
    execution: execution
      ? {
          provider: execution.provider ?? '',
          model: execution.model ?? '',
          thinking: execution.thinking ?? '',
          prompt: '',
          prompt_mode: '',
        }
      : undefined,
  } as WorkflowNodeRecord
}

describe('nodeExecutionWarning', () => {
  it('warns when an agent node declares neither provider nor model', () => {
    expect(nodeExecutionWarning(makeNode('a', 'agent'))).toBe(
      '缺 provider / model，该节点跑不起来'
    )
  })

  it('warns with the single missing key when partially configured', () => {
    expect(
      nodeExecutionWarning(makeNode('a', 'agent', { provider: 'openai' }))
    ).toBe('缺 model，该节点跑不起来')
  })

  it('stays quiet for a fully configured agent node', () => {
    expect(
      nodeExecutionWarning(
        makeNode('a', 'agent', { provider: 'openai', model: 'gpt-5' })
      )
    ).toBeUndefined()
  })

  it('stays quiet for non-agent nodes', () => {
    expect(nodeExecutionWarning(makeNode('c', 'code'))).toBeUndefined()
    expect(nodeExecutionWarning(makeNode('s', 'start'))).toBeUndefined()
    expect(nodeExecutionWarning(makeNode('g', 'approval'))).toBeUndefined()
  })

  it('warns instead of throwing when record execution holds non-string values', () => {
    const junk = {
      provider: 1,
      model: true,
      thinking: '',
      prompt: '',
      prompt_mode: '',
    } as unknown as NonNullable<WorkflowNodeRecord['execution']>
    expect(
      nodeExecutionWarning({ ...makeNode('a', 'agent'), execution: junk })
    ).toBe('缺 provider / model，该节点跑不起来')
  })
})
