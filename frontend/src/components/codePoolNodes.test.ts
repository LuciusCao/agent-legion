import { describe, expect, it } from 'vitest'
import { codePoolNodes } from './codePoolNodes'
import type { WorkflowDefinitionRecord } from '../types'

type Node = WorkflowDefinitionRecord['nodes'][number]

const node = (key: string, node_type?: string): Node =>
  ({ key, node_type }) as Node

describe('codePoolNodes', () => {
  it('never treats an agent node as a code node, routed or self-contained (#933)', () => {
    const nodes = [
      node('fetch', 'code'),
      node('legacy_agent', 'agent'),
      node('self_contained', 'agent'),
      node('old_payload'),
    ]
    const routes = [{ node_key: 'legacy_agent' }]

    expect(
      codePoolNodes(nodes, routes)
        .map((n) => n.key)
        .sort()
    ).toEqual(['fetch', 'old_payload'])
  })

  it('still honours an Agent route for payloads without node_type', () => {
    expect(codePoolNodes([node('routed')], [{ node_key: 'routed' }])).toEqual(
      []
    )
  })
})
