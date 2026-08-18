import { describe, it, expect, beforeEach } from 'vitest'
import { useConnectionStatusStore } from './connectionStatusStore'

describe('connectionStatusStore', () => {
  beforeEach(() => {
    useConnectionStatusStore.setState({ connectionStatus: {} })
  })

  it('setConnectionStatus tracks status per channel', () => {
    useConnectionStatusStore.getState().setConnectionStatus('agents', 'open')
    expect(useConnectionStatusStore.getState().connectionStatus.agents).toBe(
      'open'
    )

    useConnectionStatusStore.getState().setConnectionStatus('agents', 'closed')
    useConnectionStatusStore
      .getState()
      .setConnectionStatus('workspace', 'connecting')
    expect(useConnectionStatusStore.getState().connectionStatus).toEqual({
      agents: 'closed',
      workspace: 'connecting',
    })
  })
})
