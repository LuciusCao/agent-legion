import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useAgentsStore } from './agentsStore'
import { WebSocketMock } from '../testing/webSocketMock'
import { makeAgentStatus } from '../testing/workspaceFixtures'

const originalFetch = global.fetch

function mockWorkerStatusFetch(pausedByWorkspace: Record<string, boolean>) {
  return vi.fn().mockImplementation((url: string) => {
    const parsed = new URL(url, 'http://localhost')
    const workspaceId = parsed.searchParams.get('workspace_id') || 'ws1'
    return Promise.resolve({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () =>
        Promise.resolve({ paused: pausedByWorkspace[workspaceId] ?? true }),
      text: () =>
        Promise.resolve(
          JSON.stringify({ paused: pausedByWorkspace[workspaceId] ?? true })
        ),
    } as Response)
  })
}

describe('agentsStore', () => {
  beforeEach(() => {
    useAgentsStore.setState({
      agents: [],
      workerPausedByWorkspace: {},
    })
  })

  afterEach(() => {
    global.fetch = originalFetch
  })

  it('defaults worker paused to true for unknown workspaces', () => {
    expect(useAgentsStore.getState().getWorkerPaused('unknown')).toBe(true)
  })

  it('keeps worker paused state isolated by workspace', async () => {
    global.fetch = mockWorkerStatusFetch({
      ws1: false,
      ws2: true,
    })

    await useAgentsStore.getState().fetchWorkerStatus('ws1')
    await useAgentsStore.getState().fetchWorkerStatus('ws2')

    expect(useAgentsStore.getState().getWorkerPaused('ws1')).toBe(false)
    expect(useAgentsStore.getState().getWorkerPaused('ws2')).toBe(true)
  })

  it('connectAgentsWs returns a cleanup function', () => {
    const cleanup = useAgentsStore.getState().connectAgentsWs()
    expect(typeof cleanup).toBe('function')
    expect(() => cleanup()).not.toThrow()
  })

  describe('connectAgentsWs envelope handling', () => {
    const originalWebSocket = globalThis.WebSocket

    beforeEach(() => {
      WebSocketMock.reset()
      globalThis.WebSocket = WebSocketMock as unknown as typeof WebSocket
    })

    afterEach(() => {
      globalThis.WebSocket = originalWebSocket
    })

    it('replaces all agents on a snapshot envelope', () => {
      useAgentsStore.setState({
        agents: [makeAgentStatus({ id: 'stale', workspace_id: 'ws0' })],
      })
      const cleanup = useAgentsStore.getState().connectAgentsWs()
      WebSocketMock.instances[0].emitMessage(
        JSON.stringify({
          type: 'snapshot',
          agents: [
            makeAgentStatus({ id: 'pi', workspace_id: 'ws1' }),
            makeAgentStatus({ id: 'pi', workspace_id: 'ws2' }),
          ],
        })
      )
      expect(
        useAgentsStore.getState().agents.map((a) => a.workspace_id)
      ).toEqual(['ws1', 'ws2'])
      cleanup()
    })

    it('upserts a single agent on agent_busy / agent_idle envelopes', () => {
      const cleanup = useAgentsStore.getState().connectAgentsWs()
      const ws = WebSocketMock.instances[0]
      ws.emitMessage(
        JSON.stringify({
          type: 'snapshot',
          agents: [
            makeAgentStatus({ id: 'pi', workspace_id: 'ws1' }),
            makeAgentStatus({ id: 'pi', workspace_id: 'ws2' }),
          ],
        })
      )
      ws.emitMessage(
        JSON.stringify({
          type: 'agent_busy',
          agent: makeAgentStatus({
            id: 'pi',
            workspace_id: 'ws2',
            busy: true,
            task_count: 1,
          }),
        })
      )
      let agents = useAgentsStore.getState().agents
      expect(agents).toHaveLength(2)
      expect(agents.find((a) => a.workspace_id === 'ws1')?.busy).toBe(false)
      expect(agents.find((a) => a.workspace_id === 'ws2')?.busy).toBe(true)

      ws.emitMessage(
        JSON.stringify({
          type: 'agent_idle',
          agent: makeAgentStatus({ id: 'pi', workspace_id: 'ws2' }),
        })
      )
      agents = useAgentsStore.getState().agents
      expect(agents).toHaveLength(2)
      expect(agents.find((a) => a.workspace_id === 'ws2')?.busy).toBe(false)
      cleanup()
    })

    it('appends an unknown agent on agent_busy', () => {
      const cleanup = useAgentsStore.getState().connectAgentsWs()
      WebSocketMock.instances[0].emitMessage(
        JSON.stringify({
          type: 'agent_busy',
          agent: makeAgentStatus({ id: 'pi', workspace_id: 'ws9', busy: true }),
        })
      )
      expect(useAgentsStore.getState().agents).toHaveLength(1)
      expect(useAgentsStore.getState().agents[0].workspace_id).toBe('ws9')
      cleanup()
    })

    it('treats a legacy bare array as a full snapshot', () => {
      const cleanup = useAgentsStore.getState().connectAgentsWs()
      WebSocketMock.instances[0].emitMessage(
        JSON.stringify([makeAgentStatus({ id: 'pi', workspace_id: 'ws1' })])
      )
      expect(useAgentsStore.getState().agents).toHaveLength(1)
      expect(useAgentsStore.getState().agents[0].id).toBe('pi')
      cleanup()
    })

    it('ignores malformed messages', () => {
      const cleanup = useAgentsStore.getState().connectAgentsWs()
      WebSocketMock.instances[0].emitMessage('not json')
      expect(useAgentsStore.getState().agents).toEqual([])
      cleanup()
    })

    it('ignores a snapshot envelope without an agents array', () => {
      const cleanup = useAgentsStore.getState().connectAgentsWs()
      WebSocketMock.instances[0].emitMessage(
        JSON.stringify({ type: 'snapshot' })
      )
      WebSocketMock.instances[0].emitMessage(
        JSON.stringify({ type: 'snapshot', agents: 'oops' })
      )
      expect(useAgentsStore.getState().agents).toEqual([])
      cleanup()
    })

    it('ignores an agent_busy envelope without an agent object', () => {
      const cleanup = useAgentsStore.getState().connectAgentsWs()
      WebSocketMock.instances[0].emitMessage(
        JSON.stringify({ type: 'agent_busy' })
      )
      expect(useAgentsStore.getState().agents).toEqual([])
      cleanup()
    })
  })
})
