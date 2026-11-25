import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeSharedResource } from '../../testing/sharedResourceFixtures'
import { api } from '../../api'
import { createSharedResource } from '../../api/sharedResources'
import { ResourceSourceDialog } from './ResourceSourceDialog'

vi.mock('../../api', () => ({ api: vi.fn() }))
vi.mock('../../api/sharedResources', () => ({
  createSharedResource: vi.fn(),
  createSharedResourceVersion: vi.fn(),
}))

describe('ResourceSourceDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api).mockImplementation((path) => {
      if (String(path).includes('material-bundles')) {
        return Promise.resolve({
          bundles: [],
          total: 0,
          limit: 200,
          offset: 0,
        } as never)
      }
      return Promise.resolve({
        materials: [
          { id: 'm1', filename: 'one.md', status: 'ready' },
          { id: 'm2', filename: 'two.md', status: 'ready' },
        ],
        total: 2,
        limit: 200,
        offset: 0,
      } as never)
    })
    vi.mocked(createSharedResource)
      .mockRejectedValueOnce(new Error('copy failed'))
      .mockRejectedValueOnce(new Error('copy failed'))
      .mockResolvedValue({ resource: makeSharedResource() })
  })

  it('相同来源重试保持幂等键，变更来源使用新键', async () => {
    const user = userEvent.setup()
    render(
      <TestQueryProvider>
        <ResourceSourceDialog
          workspaceId="ws1"
          onClose={vi.fn()}
          onCreated={vi.fn()}
        />
      </TestQueryProvider>
    )
    await user.type(screen.getByLabelText('资源名称'), '审核规则')
    await user.selectOptions(screen.getByLabelText('来源'), 'material:m1')
    const submit = screen.getByRole('button', { name: '创建资源' })
    await user.click(submit)
    await waitFor(() => expect(createSharedResource).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(submit).toBeEnabled())
    await user.click(submit)
    await waitFor(() => expect(createSharedResource).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(submit).toBeEnabled())
    await user.selectOptions(screen.getByLabelText('来源'), 'material:m2')
    await user.click(submit)
    await waitFor(() => expect(createSharedResource).toHaveBeenCalledTimes(3))

    const calls = vi.mocked(createSharedResource).mock.calls
    expect(calls).toHaveLength(3)
    expect(calls[0][1].idempotencyKey).toBe(calls[1][1].idempotencyKey)
    expect(calls[2][1].idempotencyKey).not.toBe(calls[1][1].idempotencyKey)
    expect(calls[2][1].source).toEqual({ type: 'material', id: 'm2' })
  })
})
