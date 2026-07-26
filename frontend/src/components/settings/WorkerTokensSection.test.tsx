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
  isManagementAuthError: (error: unknown) =>
    error instanceof Error &&
    (error as Error & { status?: number }).status === 401,
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
  runtimes: ['local'],
}

function authError() {
  return Object.assign(new Error('Unauthorized'), { status: 401 })
}

beforeEach(() => {
  sessionStorage.clear()
  vi.clearAllMocks()
  window.confirm = vi.fn(() => true)
  mockListRegisterTokens.mockResolvedValue([sampleToken])
  mockListAgentWorkers.mockResolvedValue([sampleWorker])
})

describe('WorkerTokensSection', () => {
  it('asks for the management token when the session has none', () => {
    render(<WorkerTokensSection />)

    expect(screen.getByLabelText('管理口令')).toBeTruthy()
    expect(mockListRegisterTokens).not.toHaveBeenCalled()
  })

  it('rejects a wrong management token and shows an error', async () => {
    mockListRegisterTokens.mockRejectedValue(authError())
    render(<WorkerTokensSection />)

    fireEvent.change(screen.getByLabelText('管理口令'), {
      target: { value: 'wrong' },
    })
    fireEvent.click(screen.getByRole('button', { name: '解锁' }))

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('管理口令不正确')
    })
    expect(sessionStorage.getItem('agentWorkerMgmtToken')).toBeNull()
  })

  it('stores a valid management token in sessionStorage and loads lists', async () => {
    render(<WorkerTokensSection />)

    fireEvent.change(screen.getByLabelText('管理口令'), {
      target: { value: 'mgmt-secret' },
    })
    fireEvent.click(screen.getByRole('button', { name: '解锁' }))

    await waitFor(() => {
      expect(screen.getByText('home-mac-mini')).toBeTruthy()
    })
    expect(sessionStorage.getItem('agentWorkerMgmtToken')).toBe('mgmt-secret')
    expect(screen.getByText('mac-mini')).toBeTruthy()
    expect(screen.getByText('video_knowledge')).toBeTruthy()
  })

  it('loads lists directly when the session already holds the token', async () => {
    sessionStorage.setItem('agentWorkerMgmtToken', 'mgmt-secret')
    render(<WorkerTokensSection />)

    await waitFor(() => {
      expect(screen.getByText('home-mac-mini')).toBeTruthy()
    })
    expect(mockListRegisterTokens).toHaveBeenCalledWith('mgmt-secret')
  })

  it('clears the session token when the backend returns 401', async () => {
    sessionStorage.setItem('agentWorkerMgmtToken', 'expired')
    mockListRegisterTokens.mockRejectedValue(authError())
    render(<WorkerTokensSection />)

    await waitFor(() => {
      expect(screen.getByLabelText('管理口令')).toBeTruthy()
    })
    expect(sessionStorage.getItem('agentWorkerMgmtToken')).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('请重新输入')
  })

  it('creates a token and shows the plaintext once with a copy button', async () => {
    sessionStorage.setItem('agentWorkerMgmtToken', 'mgmt-secret')
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
    expect(mockCreateRegisterToken).toHaveBeenCalledWith('mgmt-secret', {
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
    sessionStorage.setItem('agentWorkerMgmtToken', 'mgmt-secret')
    mockRevokeRegisterToken.mockResolvedValue({ revoked: true })
    render(<WorkerTokensSection />)
    await waitFor(() => screen.getByText('home-mac-mini'))

    const item = screen.getByTestId('register-token-t1')
    fireEvent.click(item.querySelector('button') as HTMLButtonElement)

    await waitFor(() => {
      expect(mockRevokeRegisterToken).toHaveBeenCalledWith('mgmt-secret', 't1')
    })
    expect(window.confirm).toHaveBeenCalled()
  })

  it('revokes a worker after confirmation', async () => {
    sessionStorage.setItem('agentWorkerMgmtToken', 'mgmt-secret')
    mockRevokeAgentWorker.mockResolvedValue({ worker_id: 'w1', revoked: true })
    render(<WorkerTokensSection />)
    await waitFor(() => screen.getByText('mac-mini'))

    const item = screen.getByTestId('worker-w1')
    fireEvent.click(item.querySelector('button') as HTMLButtonElement)

    await waitFor(() => {
      expect(mockRevokeAgentWorker).toHaveBeenCalledWith('mgmt-secret', 'w1')
    })
    expect(window.confirm).toHaveBeenCalled()
  })
})
