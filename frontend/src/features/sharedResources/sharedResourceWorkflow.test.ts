import { describe, expect, it } from 'vitest'
import { workflowSharedResourceIds } from './useRunSharedResources'
import { sourceOptions } from './sharedResourceSource'
import type { MaterialRecord } from '../../types'

describe('shared resource workflow and sources', () => {
  it('读取代码节点的稳定资源身份并去重，不接受版本字段', () => {
    const yaml = [
      'nodes:',
      '  one:',
      '    type: code',
      '    shared_resources: [resource-1, resource-2]',
      '  two:',
      '    type: code',
      '    shared_resources: [resource-1]',
      '  legacy:',
      '    type: node',
      '    shared_resources: [resource-legacy]',
      '  agent:',
      '    type: agent',
      '    shared_resources: [resource-3]',
      '',
    ].join('\n')
    expect(workflowSharedResourceIds(yaml)).toEqual([
      'resource-1',
      'resource-2',
      'resource-legacy',
    ])
    expect(workflowSharedResourceIds('not: [valid')).toEqual([])
  })

  it('来源只包含就绪材料与文件夹', () => {
    const materials = [
      { id: 'm1', filename: 'ready.md', status: 'ready' },
      { id: 'm2', filename: 'pending.md', status: 'pending' },
    ] as MaterialRecord[]
    const options = sourceOptions(materials, [
      { id: 'b1', name: '规则文件夹' } as never,
    ])
    expect(options.map((option) => option.label)).toEqual([
      '材料 · ready.md',
      '文件夹 · 规则文件夹',
    ])
  })
})
