import { describe, expect, it } from 'vitest'
import {
  mergeNodeExecution,
  nodeExecutionWarning,
  topLevelExecutionMissing,
} from './workflowStudioExecutionWarnings'
import type {
  WorkflowDefinitionRecord,
  WorkflowNodeRecord,
} from '../../../types'

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
        }
      : undefined,
  } as WorkflowNodeRecord
}

function makeWorkflow(nodes: WorkflowNodeRecord[]): WorkflowDefinitionRecord {
  return {
    key: 'wf',
    label: 'WF',
    intake: { modes: [] },
    nodes,
    edges: [],
  } as unknown as WorkflowDefinitionRecord
}

describe('mergeNodeExecution', () => {
  const defaults = { provider: 'openai', model: 'gpt-5', thinking: 'low' }

  it('fills empty node fields from top-level defaults (node value wins)', () => {
    expect(mergeNodeExecution({ type: 'agent' }, defaults)).toEqual({
      provider: 'openai',
      model: 'gpt-5',
      thinking: 'low',
      prompt: '',
    })
    expect(
      mergeNodeExecution(
        { type: 'agent', execution: { provider: 'deepseek' } },
        defaults
      )
    ).toEqual({
      provider: 'deepseek',
      model: 'gpt-5',
      thinking: 'low',
      prompt: '',
    })
  })

  it('never inherits a top-level prompt into nodes (node-level only)', () => {
    // 顶层块本就不允许 prompt（后端 loader 拒绝），防御性再断一道。
    const withPrompt = { ...defaults, prompt: 'ignored' } as typeof defaults
    expect(mergeNodeExecution({ type: 'agent' }, withPrompt)?.prompt).toBe('')
  })

  it('exempts start nodes and leaves undeclared nodes empty without defaults', () => {
    expect(mergeNodeExecution({ type: 'start' }, defaults)).toBeUndefined()
    expect(mergeNodeExecution({ type: 'agent' }, {})).toBeUndefined()
    expect(mergeNodeExecution({ execution: { provider: 'pi' } }, {})).toEqual({
      provider: 'pi',
      model: '',
      thinking: '',
      prompt: '',
    })
  })

  it('treats non-string execution values as missing instead of throwing (codex P1)', () => {
    // 合法 YAML 非法契约值（`provider: 1`、`model: true`）不得让画布渲染
    // 抛异常：非字符串一律按未配置处理——回落顶层默认，或归为空值。
    const junk = { provider: 1, model: true } as unknown as {
      provider?: string
      model?: string
    }
    expect(
      mergeNodeExecution({ type: 'agent', execution: junk }, defaults)
    ).toEqual({
      provider: 'openai',
      model: 'gpt-5',
      thinking: 'low',
      prompt: '',
    })
    expect(mergeNodeExecution({ type: 'agent', execution: junk }, {})).toEqual({
      provider: '',
      model: '',
      thinking: '',
      prompt: '',
    })
  })
})

describe('nodeExecutionWarning', () => {
  it('warns when an agent node resolves neither provider nor model', () => {
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

  it('stays quiet for non-agent nodes (pure code workflows warn about nothing)', () => {
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
    } as unknown as NonNullable<WorkflowNodeRecord['execution']>
    expect(
      nodeExecutionWarning({ ...makeNode('a', 'agent'), execution: junk })
    ).toBe('缺 provider / model，该节点跑不起来')
  })
})

describe('topLevelExecutionMissing', () => {
  it('is true when agent nodes exist but no top-level execution defaults', () => {
    expect(
      topLevelExecutionMissing(makeWorkflow([makeNode('a', 'agent')]), {})
    ).toBe(true)
  })

  it('is false when the top-level block provides a fallback', () => {
    expect(
      topLevelExecutionMissing(makeWorkflow([makeNode('a', 'agent')]), {
        provider: 'openai',
      })
    ).toBe(false)
  })

  it('is false for pure code workflows and empty workflows', () => {
    expect(
      topLevelExecutionMissing(makeWorkflow([makeNode('c', 'code')]), {})
    ).toBe(false)
    expect(topLevelExecutionMissing(null, {})).toBe(false)
  })

  it('treats non-string top-level defaults as missing (codex P1)', () => {
    const junkDefaults = { provider: 1 } as unknown as { provider?: string }
    expect(
      topLevelExecutionMissing(
        makeWorkflow([makeNode('a', 'agent')]),
        junkDefaults
      )
    ).toBe(true)
  })
})
