import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { WorkspaceWorkersSection } from './WorkspaceWorkersSection'
import { listAgentWorkers } from '../../api'
import type { AgentWorkerSummary } from '../../api'
import { TestQueryProvider } from '../../testing/testQueryClient'

vi.mock('../../api', () => ({
  listAgentWorkers: vi.fn(),
}))

vi.mock('../../hooks/useWorkerConsoleUrl', () => ({
  useWorkerConsoleUrl: () => 'http://127.0.0.1:8789',
}))

const mockListAgentWorkers = vi.mocked(listAgentWorkers)

const WORKSPACE_ID = 'demo_video_workflow'

const sampleWorker: AgentWorkerSummary = {
  worker_id: 'w1',
  name: 'mac-mini',
  online: true,
  last_seen_at: '2026-07-26T00:00:00Z',
  revoked: false,
  allowed_workspaces: [WORKSPACE_ID],
  capabilities: [],
  labels: {},
  max_concurrency: 2,
  max_code_concurrency: 0,
  models: [],
  protocol_version: 1,
  registered_at: '2026-07-01T00:00:00Z',
  register_token_ids: [],
  runtimes: ['pi'],
}

function renderSection() {
  return render(
    <TestQueryProvider>
      <WorkspaceWorkersSection workspaceId={WORKSPACE_ID} />
    </TestQueryProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('WorkspaceWorkersSection', () => {
  it('links the empty state to the Worker console', async () => {
    mockListAgentWorkers.mockResolvedValue([])
    renderSection()

    await waitFor(() => {
      expect(screen.getByText(/本 workspace 尚无可用 Worker/)).toBeTruthy()
    })
    expect(mockListAgentWorkers).toHaveBeenCalledWith(WORKSPACE_ID)
    // 空态不再只说「在 Worker 控制台添加」：给路径，给入口。
    expect(screen.getByText(/Workspace 访问/)).toBeTruthy()
    expect(screen.getByTestId('worker-console-link').getAttribute('href')).toBe(
      'http://127.0.0.1:8789'
    )
  })

  it('links a worker row to its self-reported console address', async () => {
    mockListAgentWorkers.mockResolvedValue([
      { ...sampleWorker, labels: { console_url: 'http://10.0.0.8:8787' } },
    ])
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('workspace-worker-w1')).toBeTruthy()
    })
    const link = screen.getByTestId('worker-console-link')
    expect(link.getAttribute('href')).toBe('http://10.0.0.8:8787')
    expect(link.textContent).toContain('控制台')
  })

  it('lists the workspace workers with their online state', async () => {
    mockListAgentWorkers.mockResolvedValue([sampleWorker])
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('workspace-worker-w1')).toBeTruthy()
    })
    const item = screen.getByTestId('workspace-worker-w1')
    expect(item.textContent).toContain('mac-mini')
    expect(item.textContent).toContain('在线')
    expect(screen.queryByTestId('worker-console-link')).toBeNull()
  })
})
