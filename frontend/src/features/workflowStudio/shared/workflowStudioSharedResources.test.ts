import yaml from 'js-yaml'
import { describe, expect, it } from 'vitest'
import { patchWorkflowNodeSharedResources } from './workflowStudioYamlDraft'

describe('workflow shared resource patcher', () => {
  it('只写稳定资源身份，移除最后一个资源时删除声明', () => {
    const original = 'nodes:\n  review:\n    type: code\n    label: 审核\n'
    const next = patchWorkflowNodeSharedResources(original, 'review', [
      'resource-1',
    ])
    const parsed = yaml.load(next) as {
      nodes: { review: Record<string, unknown> }
    }
    expect(parsed.nodes.review.shared_resources).toEqual(['resource-1'])
    expect(parsed.nodes.review).not.toHaveProperty('resource_version')
    expect(parsed.nodes.review).not.toHaveProperty('manifest_hash')

    const removed = patchWorkflowNodeSharedResources(next, 'review', [])
    const empty = yaml.load(removed) as {
      nodes: { review: Record<string, unknown> }
    }
    expect(empty.nodes.review).not.toHaveProperty('shared_resources')
  })
})
