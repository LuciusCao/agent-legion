import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeSharedResource } from '../../testing/sharedResourceFixtures'
import { api } from '../../api'
import {
  createSharedResource,
  createSharedResourceVersion,
} from '../../api/sharedResources'
import { ResourceSourceDialog } from './ResourceSourceDialog'

vi.mock('../../api', () => ({ api: vi.fn() }))
vi.mock('../../api/sharedResources', () => ({
  createSharedResource: vi.fn(),
  createSharedResourceVersion: vi.fn(),
}))

function mockSecondPageSources() {
  vi.mocked(api).mockImplementation((path) => {
    const url = new URL(String(path), 'https://test.local')
    const offset = Number(url.searchParams.get('offset') ?? '0')
    if (url.pathname.includes('material-bundles')) {
      return Promise.resolve({
        bundles:
          offset === 0
            ? Array.from({ length: 200 }, (_, index) => ({
                id: `bundle-${index}`,
                name: `文件夹 ${index}`,
              }))
            : [{ id: 'bundle-ready', name: '第二页文件夹' }],
        total: 201,
        limit: 200,
        offset,
      } as never)
    }
    return Promise.resolve({
      materials:
        offset === 0
          ? Array.from({ length: 200 }, (_, index) => ({
              id: `pending-${index}`,
              filename: `pending-${index}.md`,
              status: 'pending',
            }))
          : [
              {
                id: 'material-ready',
                filename: '第二页就绪材料.md',
                status: 'ready',
              },
            ],
      total: 201,
      limit: 200,
      offset,
    } as never)
  })
}

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

  it('首页无 ready 时仍聚合第二页材料并用于创建', async () => {
    const user = userEvent.setup()
    mockSecondPageSources()
    vi.mocked(createSharedResource).mockReset().mockResolvedValue({
      resource: makeSharedResource(),
    })
    render(
      <TestQueryProvider>
        <ResourceSourceDialog
          workspaceId="ws1"
          onClose={vi.fn()}
          onCreated={vi.fn()}
        />
      </TestQueryProvider>
    )
    await user.type(screen.getByLabelText('资源名称'), '第二页资源')
    await screen.findByRole('option', { name: '材料 · 第二页就绪材料.md' })
    await user.selectOptions(
      screen.getByLabelText('来源'),
      'material:material-ready'
    )
    await user.click(screen.getByRole('button', { name: '创建资源' }))
    await waitFor(() => expect(createSharedResource).toHaveBeenCalledOnce())
    expect(createSharedResource).toHaveBeenCalledWith('ws1', {
      name: '第二页资源',
      source: { type: 'material', id: 'material-ready' },
      idempotencyKey: expect.any(String),
    })
    const paths = vi.mocked(api).mock.calls.map((call) => String(call[0]))
    expect(
      paths.some((path) => path.includes('/materials?limit=200&offset=200'))
    ).toBe(true)
    expect(
      paths.some((path) =>
        path.includes('/material-bundles?limit=200&offset=200')
      )
    ).toBe(true)
  })

  it('文件夹第二页也可用于创建新版本且携带幂等键', async () => {
    const user = userEvent.setup()
    mockSecondPageSources()
    vi.mocked(createSharedResourceVersion)
      .mockReset()
      .mockResolvedValue({
        version: {} as never,
      })
    render(
      <TestQueryProvider>
        <ResourceSourceDialog
          workspaceId="ws1"
          resource={makeSharedResource()}
          onClose={vi.fn()}
          onCreated={vi.fn()}
        />
      </TestQueryProvider>
    )
    await screen.findByRole('option', { name: '文件夹 · 第二页文件夹' })
    await user.selectOptions(
      screen.getByLabelText('来源'),
      'bundle:bundle-ready'
    )
    await user.click(screen.getByRole('button', { name: '创建新版本' }))
    await waitFor(() =>
      expect(createSharedResourceVersion).toHaveBeenCalledOnce()
    )
    expect(createSharedResourceVersion).toHaveBeenCalledWith(
      'ws1',
      'resource-1',
      {
        source: { type: 'bundle', id: 'bundle-ready' },
        idempotencyKey: expect.any(String),
      }
    )
  })
})
