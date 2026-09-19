import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { WorkerConsoleGuide } from './WorkerConsoleGuide'
import { fetchAgentWorkers } from '../../api/agentWorkers'
import { TestQueryProvider } from '../../testing/testQueryClient'

vi.mock('../../api/agentWorkers', () => ({
  fetchAgentWorkers: vi.fn(),
}))

const mockFetchAgentWorkers = vi.mocked(fetchAgentWorkers)

function renderGuide(isAdmin = true) {
  return render(
    <TestQueryProvider>
      <WorkerConsoleGuide isAdmin={isAdmin} />
    </TestQueryProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('WorkerConsoleGuide', () => {
  it('links to the configured Worker console and lists the onboarding steps', async () => {
    mockFetchAgentWorkers.mockResolvedValue({
      workers: [],
      console_url: 'http://127.0.0.1:8789',
    })
    renderGuide()

    await waitFor(() => {
      expect(screen.getByTestId('worker-console-link')).toBeTruthy()
    })
    expect(screen.getByTestId('worker-console-link').getAttribute('href')).toBe(
      'http://127.0.0.1:8789'
    )
    // 接入三步：签发 → 控制台「Workspace 访问」粘贴 → 「开始领取」。
    expect(screen.getByText(/签发新 Key/)).toBeTruthy()
    expect(screen.getByText(/Workspace 访问/)).toBeTruthy()
    expect(screen.getAllByText(/开始领取/).length).toBeGreaterThan(0)
    // 两个默认关闭的开关是「一直等待中」的首要排查点。
    expect(screen.getByText(/两个默认关闭的开关/)).toBeTruthy()
    expect(screen.queryByTestId('worker-console-unset')).toBeNull()
  })

  it('falls back to plain guidance when no console url is configured', async () => {
    mockFetchAgentWorkers.mockResolvedValue({ workers: [], console_url: '' })
    renderGuide()

    await waitFor(() => {
      expect(screen.getByTestId('worker-console-unset')).toBeTruthy()
    })
    expect(screen.getByTestId('worker-console-unset').textContent).toContain(
      'AGENT_LEGION_WORKER_CONSOLE_URL'
    )
    expect(screen.queryByTestId('worker-console-link')).toBeNull()
  })

  it('does not flash the unset hint while the address is still loading', () => {
    mockFetchAgentWorkers.mockReturnValue(new Promise(() => {}))
    renderGuide()

    expect(screen.queryByTestId('worker-console-unset')).toBeNull()
    expect(screen.queryByTestId('worker-console-link')).toBeNull()
  })

  it('tells non-admin members to ask an admin for the key', async () => {
    mockFetchAgentWorkers.mockResolvedValue({ workers: [], console_url: '' })
    renderGuide(false)

    expect(screen.getByText(/请管理员/)).toBeTruthy()
    expect(screen.queryByText(/签发新 Key/)).toBeNull()
    await waitFor(() => {
      expect(mockFetchAgentWorkers).toHaveBeenCalled()
    })
  })
})
