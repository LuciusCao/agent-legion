import { describe, it, expect, beforeEach } from 'vitest'
import { useExecutorsStore } from './executorsStore'

describe('executorsStore', () => {
  beforeEach(() => {
    useExecutorsStore.setState({ connectionStatus: {} })
  })

  it('setConnectionStatus tracks status per channel', () => {
    useExecutorsStore.getState().setConnectionStatus('agents', 'open')
    expect(useExecutorsStore.getState().connectionStatus.agents).toBe('open')

    useExecutorsStore.getState().setConnectionStatus('agents', 'closed')
    useExecutorsStore.getState().setConnectionStatus('workspace', 'connecting')
    expect(useExecutorsStore.getState().connectionStatus).toEqual({
      agents: 'closed',
      workspace: 'connecting',
    })
  })
})
