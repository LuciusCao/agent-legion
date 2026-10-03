import { act, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { AgentWorkerStatusList } from './AgentWorkerStatusList'
import { listAgentWorkers, type AgentWorkerSummary } from '../api/agentWorkers'
import { createTestQueryClient } from '../testing/testQueryClient'
import { extraQueryKeys } from '../lib/queryKeysExtra'

vi.mock('../api/agentWorkers', () => ({ listAgentWorkers: vi.fn() }))
vi.mock('../hooks/useWorkerConsoleUrl', () => ({
  useWorkerConsoleUrl: () => '',
}))

it('isolates requests and caches when switching workspace before a response arrives', async () => {
  let finishA!: (workers: AgentWorkerSummary[]) => void
  vi.mocked(listAgentWorkers).mockImplementation(async (id) => {
    if (id === 'a')
      return new Promise((resolve) => {
        finishA = resolve
      })
    if (id === 'b') return []
    throw new Error('a workspace scope is required')
  })
  const client = createTestQueryClient()
  const view = (id: string) => (
    <QueryClientProvider client={client}>
      <AgentWorkerStatusList workspaceId={id} />
    </QueryClientProvider>
  )
  const { rerender } = render(view('a'))
  await waitFor(() =>
    expect(listAgentWorkers).toHaveBeenCalledWith('a', expect.any(AbortSignal))
  )
  rerender(view('b'))
  await waitFor(() =>
    expect(client.getQueryData(extraQueryKeys.workspaceWorkers('b'))).toEqual(
      []
    )
  )
  await act(async () =>
    finishA([
      {
        worker_id: 'a-worker',
        name: 'Workspace A machine',
        allowed_workspaces: ['a'],
        runtimes: [],
        capabilities: [],
        models: [],
        max_concurrency: 1,
        max_code_concurrency: 0,
        labels: {},
        protocol_version: 1,
        registered_at: '',
        last_seen_at: '',
        online: true,
        revoked: false,
        claim_enabled: null,
      },
    ])
  )
  expect(screen.queryByText('Workspace A machine')).toBeNull()
  expect(client.getQueryData(extraQueryKeys.workspaceWorkers('b'))).toEqual([])
  expect(client.getQueryData(['agentWorkers'])).toBeUndefined()
})
