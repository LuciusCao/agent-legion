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
    })
  })

  it('rejects duplicate keys (fail-closed, draft untouched)', () => {
    expect(() =>
      appendWorkflowNode(baseYaml, { nodeType: 'code', key: 'intake' })
    ).toThrow(WorkflowNodeAppendError)
    expect(() =>
      appendWorkflowNode(baseYaml, { nodeType: 'approval', key: '_start' })
    ).toThrow(WorkflowNodeAppendError)
  })

  it('rejects empty or malformed keys', () => {
    for (const key of ['', '  ', 'has space', 'a:b']) {
      expect(() =>
        appendWorkflowNode(baseYaml, { nodeType: 'code', key })
      ).toThrow(WorkflowNodeAppendError)
    }
  })
})
