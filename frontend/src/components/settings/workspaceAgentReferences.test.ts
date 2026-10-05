import { describe, expect, it } from 'vitest'
import type { AgentListItem } from '../../types'
import {
  isDraftOnly,
  pendingDraftCapability,
  routedCapability,
} from './workspaceAgentReferences'

function item(overrides: Partial<AgentListItem>): AgentListItem {
  return {
    agent_id: 'a',
    capability: 'cap_a',
    runtime: 'pi',
    skill: '',
    version: 1,
    status: 'published',
    has_draft: false,
    published_capability: 'cap_a',
    published_version: 1,
    ...overrides,
  }
}

describe('workspaceAgentReferences (#906)', () => {
  it('routes by the published capability when a draft renames it', () => {
    const agent = item({
      status: 'draft',
      capability: 'cap_b',
      version: 2,
      has_draft: true,
    })
    expect(isDraftOnly(agent)).toBe(false)
    expect(routedCapability(agent)).toBe('cap_a')
    expect(pendingDraftCapability(agent)).toBe('cap_b')
  })

  it('falls back to the draft capability for never-published agents', () => {
    const agent = item({
      status: 'draft',
      capability: 'cap_d',
      published_capability: null,
      published_version: null,
    })
    expect(isDraftOnly(agent)).toBe(true)
    expect(routedCapability(agent)).toBe('cap_d')
    expect(pendingDraftCapability(agent)).toBeNull()
  })

  it('reports no pending draft capability when it matches the published one', () => {
    expect(pendingDraftCapability(item({ status: 'draft' }))).toBeNull()
    expect(pendingDraftCapability(item({}))).toBeNull()
  })
})
