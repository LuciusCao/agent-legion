import { describe, expect, it } from 'vitest'
import { approvalReworkCandidates } from './workflowStudioApprovalRework'

// 与 nodeTypeSections.test 的组件级用例互补：这里直接钉纯函数的
// 判定源语义（after ∪ edges、start 排除、闭包传递、下游/平行排除）。
const dagYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  fetch:',
  '    type: code',
  '    capability: fetch',
  '    after: [_start]',
  '  draft:',
  '    type: agent',
  '    capability: draft',
  '    after: [fetch]',
  '  side:',
  '    type: code',
  '    capability: side',
  '    after: [_start]',
  '  gate:',
  '    type: approval',
  '    after: [draft]',
  '  publish:',
  '    type: code',
  '    capability: publish',
  '    after: [gate]',
  '',
].join('\n')

describe('approvalReworkCandidates', () => {
  it('returns the transitive ancestor closure excluding start/downstream/parallel', () => {
    // gate 的祖先闭包 = {draft, fetch, _start}；排除 start → [draft, fetch]。
    // side（平行）与 publish（下游）不在候选内。
    expect(approvalReworkCandidates(dagYaml, 'gate')).toEqual([
      'draft',
      'fetch',
    ])
  })

  it('follows edges-array dependencies too (v2 yaml without after)', () => {
    const edgesOnlyYaml = [
      'key: demo',
      'schema_version: 2',
      'nodes:',
      '  _start:',
      '    type: start',
      '  fetch:',
      '    type: code',
      '    capability: fetch',
      '  gate:',
      '    type: approval',
      'edges:',
      '  - {from: _start, to: fetch}',
      '  - {from: fetch, to: gate}',
      '',
    ].join('\n')
    expect(approvalReworkCandidates(edgesOnlyYaml, 'gate')).toEqual(['fetch'])
  })

  it('returns empty for a root approval gate (only start upstream)', () => {
    const rootYaml = dagYaml.replace('after: [draft]', 'after: [_start]')
    expect(approvalReworkCandidates(rootYaml, 'gate')).toEqual([])
  })
})
