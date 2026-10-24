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
          prompt_mode: '',
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
      prompt_mode: '',
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
      prompt_mode: '',
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
      prompt_mode: '',
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
      prompt_mode: '',
    })
    expect(mergeNodeExecution({ type: 'agent', execution: junk }, {})).toEqual({
      provider: '',
      model: '',
      thinking: '',
      prompt: '',
      prompt_mode: '',
    })
  })

  it('carries the node-declared runtime through even without top-level defaults (#1180)', () => {
    // r3 评审回归：漏丢 runtime 键会让草稿画布的 agent·runtime 类型徽标
    // 恒无 runtime（只读 revision 视图走后端 loader，不受影响）。
    expect(
      mergeNodeExecution(
        { type: 'agent', execution: { runtime: 'velites' } },
        {}
      )
    ).toEqual({
      provider: '',
      model: '',
      thinking: '',
      prompt: '',
      prompt_mode: '',
      runtime: 'velites',
    })
  })

  it('merges the top-level runtime default into agent nodes only (#933)', () => {
    const withRuntime = { ...defaults, runtime: 'pi' }
    expect(mergeNodeExecution({ type: 'agent' }, withRuntime)?.runtime).toBe(
      'pi'
    )
    // 节点声明值优先于顶层默认。
    expect(
      mergeNodeExecution(
        { type: 'agent', execution: { runtime: 'velites' } },
        withRuntime
      )?.runtime
    ).toBe('velites')
    // code 节点不携带 runtime（快照重载会失败，对齐后端 loader）。
    const codeMerged = mergeNodeExecution({ type: 'code' }, withRuntime)
    expect(codeMerged && 'runtime' in codeMerged).toBe(false)
    // start 节点整体豁免合并。
    expect(mergeNodeExecution({ type: 'start' }, withRuntime)).toBeUndefined()
    // 空 runtime 不出键，保持记录形状与未配置时一致。
    expect(
      mergeNodeExecution({ type: 'agent' }, defaults) &&
        'runtime' in mergeNodeExecution({ type: 'agent' }, defaults)!
    ).toBe(false)
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
      prompt_mode: '',
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

  it('is false when every agent node has effective provider/model without defaults (#666)', () => {
    // 顶层默认缺席但节点各自配齐（有效值已经 mergeNodeExecution 合并）——
    // 没有真实缺口，不整体误报。
    expect(
      topLevelExecutionMissing(
        makeWorkflow([
          makeNode('a', 'agent', { provider: 'openai', model: 'gpt-5' }),
          makeNode('b', 'agent', { provider: 'deepseek', model: 'r1' }),
        ]),
        {}
      )
    ).toBe(false)
  })

  it('is true when at least one agent node lacks effective provider/model', () => {
    expect(
      topLevelExecutionMissing(
        makeWorkflow([
          makeNode('a', 'agent', { provider: 'openai', model: 'gpt-5' }),
          makeNode('b', 'agent', { provider: 'deepseek' }),
        ]),
        {}
      )
    ).toBe(true)
  })
})
