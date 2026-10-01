import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { WorkerConsoleGuide } from './WorkerConsoleGuide'
import { fetchWorkerConsole } from '../../api/agentWorkers'
import { TestQueryProvider } from '../../testing/testQueryClient'

vi.mock('../../api/agentWorkers', () => ({
  fetchWorkerConsole: vi.fn(),
}))

const mockFetchWorkerConsole = vi.mocked(fetchWorkerConsole)

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
  it('reports initial failure as unknown and lets the user retry', async () => {
    mockFetchWorkerConsole.mockRejectedValueOnce(new Error('offline'))
    renderGuide()
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法获取')
    expect(screen.queryByTestId('worker-console-unset')).toBeNull()
    mockFetchWorkerConsole.mockResolvedValue({
      console_url: 'http://localhost:8787',
    })
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByTestId('worker-console-link')).toHaveAttribute(
      'href',
      'http://localhost:8787'
    )
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('links to the configured Worker console and lists the onboarding steps', async () => {
    mockFetchWorkerConsole.mockResolvedValue({
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
    expect(screen.getAllByText(/Workspace 访问/).length).toBeGreaterThan(0)
    expect(screen.getByText('Worker 控制台要求控制令牌？')).toBeTruthy()
    expect(
      screen.getByText(/docker compose -f deploy\/compose.host.yaml/)
        .textContent
    ).toContain('/var/lib/agent-legion-worker-control/control_token')
    expect(screen.getAllByText(/开始领取/).length).toBeGreaterThan(0)
    // 两个默认关闭的开关是「一直等待中」的首要排查点。
    expect(screen.getByText(/两个默认关闭的开关/)).toBeTruthy()
    expect(screen.queryByTestId('worker-console-unset')).toBeNull()
  })

  it('falls back to plain guidance when no console url is configured', async () => {
    mockFetchWorkerConsole.mockResolvedValue({ console_url: '' })
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
    mockFetchWorkerConsole.mockReturnValue(new Promise(() => {}))
    renderGuide()

    expect(screen.queryByTestId('worker-console-unset')).toBeNull()
    expect(screen.queryByTestId('worker-console-link')).toBeNull()
  })

  it('tells non-admin members to ask an admin for the key', async () => {
    mockFetchWorkerConsole.mockResolvedValue({ console_url: '' })
    renderGuide(false)

    expect(screen.getByText(/请管理员/)).toBeTruthy()
    expect(screen.queryByText(/签发新 Key/)).toBeNull()
    await waitFor(() => {
      expect(mockFetchWorkerConsole).toHaveBeenCalled()
    })
  })
})
