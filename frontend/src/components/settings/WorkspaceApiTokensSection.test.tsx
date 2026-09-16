import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkspaceApiTokensSection } from './WorkspaceApiTokensSection'
import {
  createWorkspaceApiToken,
  listWorkspaceApiTokens,
  revokeWorkspaceApiToken,
} from '../../api'
import { TestQueryProvider } from '../../testing/testQueryClient'

vi.mock('../../api', () => ({
  createWorkspaceApiToken: vi.fn(),
  listWorkspaceApiTokens: vi.fn(),
  revokeWorkspaceApiToken: vi.fn(),
}))

const mockList = vi.mocked(listWorkspaceApiTokens)
const mockCreate = vi.mocked(createWorkspaceApiToken)
const mockRevoke = vi.mocked(revokeWorkspaceApiToken)

const WORKSPACE_ID = 'demo_video_workflow'

const sampleToken = {
  token_id: 'tok-1',
  label: 'cms-cron',
  workspace_id: WORKSPACE_ID,
  created_at: '2026-09-01T00:00:00Z',
  expires_at: null,
  revoked: false,
  last_used_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  mockList.mockResolvedValue([sampleToken])
})

function renderSection() {
  return render(
    <TestQueryProvider>
      <WorkspaceApiTokensSection workspaceId={WORKSPACE_ID} />
    </TestQueryProvider>
  )
}

describe('WorkspaceApiTokensSection', () => {
  it('lists the workspace tokens without any credential material', async () => {
    renderSection()

    await waitFor(() => {
      expect(screen.getByText('cms-cron')).toBeTruthy()
    })
    expect(mockList).toHaveBeenCalledWith(WORKSPACE_ID)
    const row = screen.getByTestId('api-token-tok-1')
    expect(row.textContent).toContain('tok-1')
    expect(row.textContent).not.toContain('secret')
    expect(screen.getByText('未使用')).toBeTruthy()
  })

  it('issues a token with label and TTL, showing the plaintext once', async () => {
    mockCreate.mockResolvedValue({
      token_id: 'tok-2',
      api_token: 'tok-2.secret-value',
      workspace_id: WORKSPACE_ID,
      label: 'form-agent',
    })
    renderSection()

    fireEvent.change(screen.getByLabelText('API Token 名称'), {
      target: { value: 'form-agent' },
    })
    fireEvent.change(screen.getByLabelText('API Token 有效期（小时）'), {
      target: { value: '48' },
    })
    fireEvent.click(screen.getByRole('button', { name: '签发' }))

    await waitFor(() => {
      expect(mockCreate).toHaveBeenCalledWith(WORKSPACE_ID, {
        label: 'form-agent',
        ttl_hours: 48,
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('created-api-token')).toBeTruthy()
    })
    expect(screen.getByText('tok-2.secret-value')).toBeTruthy()
  })

  it('issues without TTL when the field is left empty', async () => {
    mockCreate.mockResolvedValue({
      token_id: 'tok-3',
      api_token: 'tok-3.s',
      workspace_id: WORKSPACE_ID,
      label: 'no-ttl',
    })
    renderSection()

    fireEvent.change(screen.getByLabelText('API Token 名称'), {
      target: { value: 'no-ttl' },
    })
    fireEvent.click(screen.getByRole('button', { name: '签发' }))

    await waitFor(() => {
      expect(mockCreate).toHaveBeenCalledWith(WORKSPACE_ID, {
        label: 'no-ttl',
        ttl_hours: undefined,
      })
    })
  })

  it('refuses a non-integer TTL locally without calling the API', async () => {
    renderSection()

    fireEvent.change(screen.getByLabelText('API Token 名称'), {
      target: { value: 'bad' },
    })
    fireEvent.change(screen.getByLabelText('API Token 有效期（小时）'), {
      target: { value: 'x' },
    })
    fireEvent.click(screen.getByRole('button', { name: '签发' }))

    expect(mockCreate).not.toHaveBeenCalled()
    expect(
      screen.getByText('有效期必须是正整数小时，或留空表示永不过期')
    ).toBeTruthy()
  })

  it('revokes via the confirm dialog and refreshes', async () => {
    mockRevoke.mockResolvedValue({ token_id: 'tok-1', revoked: true })
    renderSection()

    await waitFor(() => {
      expect(screen.getByText('cms-cron')).toBeTruthy()
    })
    fireEvent.click(screen.getByRole('button', { name: '吊销' }))
    // The list row's 吊销 opens the dialog; the dialog's confirm button
    // (label 吊销, in the dialog) submits — pick the last match.
    const revokeButtons = screen.getAllByRole('button', { name: '吊销' })
    fireEvent.click(revokeButtons[revokeButtons.length - 1])

    await waitFor(() => {
      expect(mockRevoke).toHaveBeenCalledWith(WORKSPACE_ID, 'tok-1')
    })
  })

  it('surfaces list errors', async () => {
    mockList.mockRejectedValueOnce(new Error('boom'))
    renderSection()

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('boom')
    })
  })
})
