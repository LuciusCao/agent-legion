import { describe, expect, it } from 'vitest'
import { codeNodeKeys } from './codeNodes'

describe('codeNodeKeys', () => {
  it('never treats an agent node as a code node, routed or self-contained (#933)', () => {
    const nodes = [
      { key: 'fetch', node_type: 'code' },
      { key: 'legacy_agent', node_type: 'agent' },
      { key: 'self_contained', node_type: 'agent' },
      { key: 'old_payload' },
    ]
    const routes = [{ node_key: 'legacy_agent' }]

    expect([...codeNodeKeys(nodes, routes)].sort()).toEqual([
      'fetch',
      'old_payload',
    ])
  })

  it('still honours an Agent route for payloads without node_type', () => {
    expect([
      ...codeNodeKeys([{ key: 'routed' }], [{ node_key: 'routed' }]),
    ]).toEqual([])
  })
})
