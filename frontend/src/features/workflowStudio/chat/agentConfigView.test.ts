import { describe, expect, it } from 'vitest'
import { agentConfigView, flattenOptions } from './agentConfigView'
import type { StudioChatSessionRecord } from './studioChatApi'

function session(
  overrides: Partial<StudioChatSessionRecord>
): StudioChatSessionRecord {
  return {
    id: 's1',
    workspace_id: 'ws1',
    user_id: 'u1',
    agent_id: 'kimi',
    title: '',
    status: 'idle',
    acp_session_id: 'acp-1',
    capability_snapshot: { sessionModes: true, sessionConfigOptions: true },
    allow_all_permissions: false,
    mcp_status: 'unknown',
    selected_node_key: null,
    error_detail: '',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    closed_at: null,
    ...overrides,
  }
}

const MODEL = {
  id: 'model',
  name: 'Model',
  category: 'model',
  type: 'select',
  currentValue: 'k3',
  options: [
    { group: 'kimi', name: 'Kimi', options: [{ value: 'k3', name: 'K3' }] },
  ],
}
const THINKING = {
  id: 'thinking',
  category: 'thought_level',
  type: 'select',
  currentValue: 'high',
  options: [{ value: 'low' }, { value: 'high' }, { value: 'max' }],
}

describe('agentConfigView', () => {
  it('hides the whole surface for agents that advertise neither capability', () => {
    const view = agentConfigView(
      session({
        capability_snapshot: {},
        session_modes: null,
        config_options: null,
      })
    )
    expect(view.visible).toBe(false)
    expect(agentConfigView(null).visible).toBe(false)
  })

  it('routes entries by category: model / thought_level / everything else → advanced', () => {
    const view = agentConfigView(
      session({
        session_modes: {
          currentModeId: 'default',
          availableModes: [{ id: 'default', name: 'Default' }, { id: 'plan' }],
        },
        config_options: [
          MODEL,
          THINKING,
          { id: 'verbose', type: 'boolean', currentValue: false },
          {
            id: 'tone',
            category: '_tone',
            type: 'select',
            currentValue: 'a',
            options: [],
          },
        ],
      })
    )
    expect(view.visible).toBe(true)
    expect(view.modes?.available.map((m) => m.name)).toEqual([
      'Default',
      'plan',
    ])
    expect(view.model?.id).toBe('model')
    expect(view.thought?.map.toNative.medium).toBe('low')
    expect(view.advanced.map((e) => e.id)).toEqual(['verbose', 'tone'])
    expect(view.advanced[0].currentValue).toBe('false')
  })

  it('flattens grouped options for mapping while keeping groups for rendering', () => {
    const view = agentConfigView(session({ config_options: [MODEL] }))
    expect(view.model?.options).toHaveLength(1)
    expect('group' in view.model!.options[0]).toBe(true)
    expect(flattenOptions(view.model!.options)).toEqual([
      { value: 'k3', name: 'K3' },
    ])
  })
})
