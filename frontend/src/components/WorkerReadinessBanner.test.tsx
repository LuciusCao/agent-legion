import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkerReadinessBanner } from './WorkerReadinessBanner'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { createMockAgentsState } from '../testing/fixtures'
import type { AgentWorkerSummary } from '../api/agentWorkers'
import { listAgentWorkers } from '../api'

vi.mock('../api', () => ({
  listAgentWorkers: vi.fn(),
}))

vi.mock('../hooks/useWorkerConsoleUrl', () => ({
  useWorkerConsoleUrl: () => 'http://127.0.0.1:8789',
}))

const setWorkerPausedMock = vi.fn()
let mockPaused = false

vi.mock('../stores/agentsStore', () => ({
  useAgentsStore: (
    selector?: (state: ReturnType<typeof createMockAgentsState>) => unknown
  ) => {
    const state = createMockAgentsState({
      getWorkerPaused: () => mockPaused,
      setWorkerPaused: setWorkerPausedMock,
    })
    return selector ? selector(state) : state
  },
}))

const mockListAgentWorkers = vi.mocked(listAgentWorkers)

function worker(
  overrides: Partial<AgentWorkerSummary> = {}
): AgentWorkerSummary {
  return {
    worker_id: 'w1',
    name: 'mac',
    runtimes: ['pi'],
    capabilities: [],
    models: [],
    max_concurrency: 1,
    max_code_concurrency: 0,
    labels: {},
    protocol_version: 1,
    allowed_workspaces: ['ws1'],
    register_token_ids: [],
    registered_at: '2026-09-01T00:00:00Z',
    last_seen_at: '2026-09-01T00:00:00Z',
    online: true,
    revoked: false,
    claim_enabled: true,
    ...overrides,
  }
}

function renderBanner(
  props: Partial<Parameters<typeof WorkerReadinessBanner>[0]> = {}
) {
  return render(
    <MemoryRouter>
      <WorkerReadinessBanner
        workspaceId="ws1"
        waitingCount={3}
        needsWorker
        {...props}
      />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPaused = false
  mockListAgentWorkers.mockResolvedValue([worker()])
})

describe('WorkerReadinessBanner', () => {
  it('stays hidden without waiting jobs or when everything is ready', async () => {
    renderBanner({ waitingCount: 0 })
    expect(screen.queryByTestId('worker-readiness-banner')).toBeNull()

    renderBanner()
    await waitFor(() =>
      expect(mockListAgentWorkers).toHaveBeenCalledWith('ws1')
    )
    expect(screen.queryByTestId('worker-readiness-banner')).toBeNull()
  })

  it('offers to resume scheduling when the workspace is paused', async () => {
    mockPaused = true
    renderBanner()
    const banner = await screen.findByTestId('worker-readiness-banner')
    expect(banner.textContent).toContain('3 个任务在等待中')
    expect(banner.textContent).toContain('调度已暂停')
    fireEvent.click(screen.getByRole('button', { name: '恢复调度' }))
    expect(setWorkerPausedMock).toHaveBeenCalledWith(false, 'ws1')
  })

  it('points at worker onboarding when no worker is online', async () => {
    mockListAgentWorkers.mockResolvedValue([worker({ online: false })])
    renderBanner()
    await screen.findByText(/没有在线的 Worker/)
    expect(screen.getByRole('button', { name: '去接入 Worker' })).toBeTruthy()
    expect(screen.getByTestId('worker-console-link')).toHaveAttribute(
      'href',
      'http://127.0.0.1:8789'
    )
  })

  it('names the idle claim switch and links the worker own console', async () => {
    mockListAgentWorkers.mockResolvedValue([
      worker({
        claim_enabled: false,
        labels: { console_url: 'http://10.0.0.8:8787' },
      }),
    ])
    renderBanner()
    await screen.findByText(/未开始领取/)
    expect(screen.getByTestId('worker-console-link')).toHaveAttribute(
      'href',
      'http://10.0.0.8:8787'
    )
  })

  it('skips worker checks for pure code workflows', async () => {
    mockListAgentWorkers.mockResolvedValue([])
    renderBanner({ needsWorker: false })
    expect(screen.queryByTestId('worker-readiness-banner')).toBeNull()
  })
})
