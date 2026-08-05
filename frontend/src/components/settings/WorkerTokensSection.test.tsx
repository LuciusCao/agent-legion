import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkerTokensSection } from './WorkerTokensSection'
import {
  createRegisterToken,
  listAgentWorkers,
  listRegisterTokens,
  revokeAgentWorker,
  revokeRegisterToken,
} from '../../api'

vi.mock('../../api', () => ({
  createRegisterToken: vi.fn(),
  listAgentWorkers: vi.fn(),
  listRegisterTokens: vi.fn(),
  revokeAgentWorker: vi.fn(),
  revokeRegisterToken: vi.fn(),
}))

const mockListRegisterTokens = vi.mocked(listRegisterTokens)
const mockListAgentWorkers = vi.mocked(listAgentWorkers)
const mockCreateRegisterToken = vi.mocked(createRegisterToken)
const mockRevokeRegisterToken = vi.mocked(revokeRegisterToken)
const mockRevokeAgentWorker = vi.mocked(revokeAgentWorker)

const sampleToken = {
  token_id: 't1',
  label: 'home-mac-mini',
  workspace_id: 'video_knowledge',
  created_at: '2026-07-01T00:00:00Z',
  revoked: false,
}

const sampleWorker = {
  worker_id: 'w1',
  name: 'mac-mini',
  online: true,
  last_seen_at: '2026-07-26T00:00:00Z',
  revoked: false,
  allowed_workspaces: [],
  capabilities: [],
  labels: {},
  max_concurrency: 2,
  models: [],
  protocol_version: 1,
  registered_at: '2026-07-01T00:00:00Z',
  runtimes: ['pi'],
}

beforeEach(() => {
  vi.clearAllMocks()
  window.confirm = vi.fn(() => true)
  mockListRegisterTokens.mockResolvedValue([sampleToken])
  mockListAgentWorkers.mockResolvedValue([sampleWorker])
})

describe('WorkerTokensSection', () => {
  it('loads token and worker lists on mount without any credential', async () => {
    render(<WorkerTokensSection />)

    await waitFor(() => {
      expect(screen.getByText('home-mac-mini')).toBeTruthy()
    })
    expect(mockListRegisterTokens).toHaveBeenCalledWith()
    expect(mockListAgentWorkers).toHaveBeenCalledWith()
    expect(screen.getByText('mac-mini')).toBeTruthy()
    expect(screen.getByText('video_knowledge')).toBeTruthy()
  })

  it('shows runtime, concurrency and workspace scope chips for workers', async () => {
    mockListAgentWorkers.mockResolvedValue([
      sampleWorker,
      {
        ...sampleWorker,
        worker_id: 'w2',
        name: 'scoped-mac',
        online: false,
        allowed_workspaces: ['video_knowledge', 'question_comprehension'],
      },
    ])
    render(<WorkerTokensSection />)

    await waitFor(() => {
      expect(screen.getByTestId('worker-w1')).toBeTruthy()
    })
    const globalItem = screen.getByTestId('worker-w1')
    expect(globalItem.textContent).toContain('在线')
    expect(globalItem.textContent).toContain('pi')
    expect(globalItem.textContent).toContain('并发上限 2')
    expect(globalItem.textContent).toContain('全部 workspace')

    const scopedItem = screen.getByTestId('worker-w2')
    expect(scopedItem.textContent).toContain('离线')
    expect(scopedItem.textContent).toContain(
      'video_knowledge, question_comprehension'
    )
  })

  it('shows an error when loading fails', async () => {
    mockListRegisterTokens.mockRejectedValue(new Error('HTTP 500'))
    render(<WorkerTokensSection />)

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('HTTP 500')
    })
  })

  it('creates a token and shows the plaintext once with a copy button', async () => {
    mockCreateRegisterToken.mockResolvedValue({
      token_id: 't2',
      register_token: 'plain-secret',
      workspace_id: null,
      label: 'new-worker',
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    render(<WorkerTokensSection />)
    await waitFor(() => screen.getByText('home-mac-mini'))

    fireEvent.change(screen.getByLabelText('Token 标签'), {
      target: { value: 'new-worker' },
    })
    fireEvent.click(screen.getByRole('button', { name: '签发' }))

    await waitFor(() => {
      expect(screen.getByTestId('created-token')).toBeTruthy()
    })
    expect(mockCreateRegisterToken).toHaveBeenCalledWith({
      label: 'new-worker',
      workspace_id: null,
    })
    expect(screen.getByText('plain-secret')).toBeTruthy()
    expect(screen.getByText(/仅显示这一次/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '复制 Token' }))
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('plain-secret')
    })
  })

  it('revokes a register token after confirmation', async () => {
    mockRevokeRegisterToken.mockResolvedValue({ revoked: true })
    render(<WorkerTokensSection />)
    await waitFor(() => screen.getByText('home-mac-mini'))

    const item = screen.getByTestId('register-token-t1')
    fireEvent.click(item.querySelector('button') as HTMLButtonElement)

    await waitFor(() => {
      expect(mockRevokeRegisterToken).toHaveBeenCalledWith('t1')
    })
    expect(window.confirm).toHaveBeenCalled()
  })

  it('revokes a worker after confirmation', async () => {
    mockRevokeAgentWorker.mockResolvedValue({ worker_id: 'w1', revoked: true })
    render(<WorkerTokensSection />)
    await waitFor(() => screen.getByText('mac-mini'))

    const item = screen.getByTestId('worker-w1')
    fireEvent.click(item.querySelector('button') as HTMLButtonElement)

    await waitFor(() => {
      expect(mockRevokeAgentWorker).toHaveBeenCalledWith('w1')
    })
    expect(window.confirm).toHaveBeenCalled()
  })
})
