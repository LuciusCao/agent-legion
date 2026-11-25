import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './core'
import { listSharedResources } from './sharedResources'
import { makeSharedResource } from '../testing/sharedResourceFixtures'

vi.mock('./core', () => ({ api: vi.fn() }))

describe('sharedResources API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('读取后续页，避免把第一页之外的声明资源误判为不可用', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        resources: Array.from({ length: 200 }, (_, index) =>
          makeSharedResource({ id: `resource-${index}` })
        ),
        total: 201,
        limit: 200,
        offset: 0,
      })
      .mockResolvedValueOnce({
        resources: [makeSharedResource({ id: 'resource-200' })],
        total: 201,
        limit: 200,
        offset: 200,
      })

    const result = await listSharedResources('ws1')

    expect(result.resources).toHaveLength(201)
    expect(result.resources[200].id).toBe('resource-200')
    expect(vi.mocked(api).mock.calls[1][0]).toContain('offset=200')
    expect(result).toMatchObject({ total: 201, limit: 201, offset: 0 })
  })
})
