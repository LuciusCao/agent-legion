import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import {
  appendWorkflowNode,
  WorkflowNodeAppendError,
} from './workflowStudioYamlDraft.appendNode'

const baseYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    type: code',
  '    capability: intake',
  '    after: [_start]',
  '',
].join('\n')

type RawYaml = { nodes?: Record<string, Record<string, unknown>> }

function parseNodes(raw: string): RawYaml {
  return yaml.load(raw) as RawYaml
}

describe('appendWorkflowNode', () => {
  it('appends a code node with capability/label defaulting to key', () => {
    const out = appendWorkflowNode(baseYaml, { nodeType: 'code', key: 'draft' })
    const node = parseNodes(out).nodes?.draft
    expect(node).toEqual({
      type: 'code',
      label: 'draft',
      capability: 'draft',
      after: [],
    })
    // 既有节点不动。
    expect(parseNodes(out).nodes?.intake?.capability).toBe('intake')
  })

  it('appends an approval node without capability', () => {
    const out = appendWorkflowNode(baseYaml, {
      nodeType: 'approval',
      key: 'gate',
    })
    const node = parseNodes(out).nodes?.gate
    expect(node).toEqual({ type: 'approval', label: 'gate', after: [] })
    expect(node).not.toHaveProperty('capability')
  })

  it('keeps explicit label and capability', () => {
    const out = appendWorkflowNode(baseYaml, {
      nodeType: 'agent',
      key: 'gen',
      label: '生成关键信息',
      capability: 'generate_key_info',
    })
    expect(parseNodes(out).nodes?.gen).toEqual({
      type: 'agent',
      label: '生成关键信息',
      capability: 'generate_key_info',
      after: [],
      // #935：agent 节点自含执行档案——无顶层默认时写默认 runtime。
      execution: { runtime: 'velites' },
    })
  })

  it('lets a new agent node inherit the workflow top-level runtime (#935)', () => {
    const out = appendWorkflowNode(`execution:\n  runtime: pi\n${baseYaml}`, {
      nodeType: 'agent',
      key: 'gen',
    })
    expect(parseNodes(out).nodes?.gen).not.toHaveProperty('execution')
  })

  it('never writes a runtime onto a new code node (#935)', () => {
    const out = appendWorkflowNode(baseYaml, { nodeType: 'code', key: 'c2' })
    expect(parseNodes(out).nodes?.c2).not.toHaveProperty('execution')
  })

  it('rejects duplicate keys (fail-closed, draft untouched)', () => {
    expect(() =>
      appendWorkflowNode(baseYaml, { nodeType: 'code', key: 'intake' })
    ).toThrow(WorkflowNodeAppendError)
    expect(() =>
      appendWorkflowNode(baseYaml, { nodeType: 'approval', key: '_start' })
    ).toThrow(WorkflowNodeAppendError)
  })

  it('rejects malformed keys', () => {
    for (const key of ['', '  ', 'has space', 'a:b']) {
      expect(() =>
        appendWorkflowNode(baseYaml, { nodeType: 'code', key })
      ).toThrow(WorkflowNodeAppendError)
    }
  })

  it('rejects structurally malformed drafts before touching nodes (codex P2 on #400)', () => {
    // 语法合法但 nodes 是数组/字符串、或某既有节点不是 mapping 的草稿：
    // 对象展开会把索引当节点键、覆盖保存时不可逆破坏草稿——必须拒绝。
    const nodesAsArray = 'key: demo\nnodes:\n  - _start\n  - intake\n'
    expect(() =>
      appendWorkflowNode(nodesAsArray, { nodeType: 'code', key: 'draft' })
    ).toThrow('草稿结构异常')
    const nodesAsString = 'key: demo\nnodes: intake\n'
    expect(() =>
      appendWorkflowNode(nodesAsString, { nodeType: 'code', key: 'draft' })
    ).toThrow('草稿结构异常')
    const scalarNode =
      'key: demo\nnodes:\n  _start:\n    type: start\n  intake: just-a-string\n'
    expect(() =>
      appendWorkflowNode(scalarNode, { nodeType: 'code', key: 'draft' })
    ).toThrow('草稿结构异常')
    // edges 非数组：渲染侧会回退 published 画布，写路径同步拒绝，避免
    // 「toast 已添加但画布不显示新节点」的脱节（subagent P3 on #400）。
    const badEdges =
      'key: demo\nnodes:\n  _start:\n    type: start\nedges: not-a-list\n'
    expect(() =>
      appendWorkflowNode(badEdges, { nodeType: 'code', key: 'draft' })
    ).toThrow('草稿结构异常')
  })
})
