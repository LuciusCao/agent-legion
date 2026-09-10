import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  cancelCampaign,
  createCampaign,
  createCampaignFromManifest,
  createSubmitCampaign,
  fetchCampaign,
  fetchCampaigns,
  pauseCampaign,
  previewCampaign,
  resumeCampaign,
} from './campaignApi'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

function mockFetchJson(response: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(response),
    text: () => Promise.resolve(JSON.stringify(response)),
  } as Response)
}

describe('campaignApi', () => {
  it('creates a rerun campaign via the JSON body with the name', async () => {
    const fetchMock = mockFetchJson({ campaign: { id: 'c1' } })
    global.fetch = fetchMock

    await createCampaign(
      'ws1',
      'rerun',
      {
        from_failed_node: true,
        node_key: null,
        filter: { status: 'failed', workflow_version_none: false },
        job_ids: null,
      },
      '重跑 · 全部失败任务'
    )

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/campaigns',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          mode: 'rerun',
          name: '重跑 · 全部失败任务',
          rerun: {
            from_failed_node: true,
            node_key: null,
            filter: { status: 'failed', workflow_version_none: false },
            job_ids: null,
          },
        }),
      })
    )
  })

  it('defaults the name to empty when not provided', async () => {
    const fetchMock = mockFetchJson({ campaign: { id: 'c1' } })
    global.fetch = fetchMock

    await createCampaign('ws1', 'rerun', {
      from_failed_node: true,
      node_key: null,
      filter: { status: 'failed', workflow_version_none: false },
      job_ids: null,
    })

    const body = JSON.parse(
      (fetchMock.mock.calls[0] as [string, RequestInit])[1].body as string
    )
    expect(body).toEqual({
      mode: 'rerun',
      name: '',
      rerun: {
        from_failed_node: true,
        node_key: null,
        filter: { status: 'failed', workflow_version_none: false },
        job_ids: null,
      },
    })
  })

  it('creates a submit campaign with inline items', async () => {
    const fetchMock = mockFetchJson({ campaign: { id: 'c1' } })
    global.fetch = fetchMock

    await createSubmitCampaign('ws1', {
      items: [{ type: 'ref', connection_key: 'cms', external_id: 'a1' }],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/campaigns',
      expect.objectContaining({
        body: JSON.stringify({
          mode: 'submit',
          name: '',
          submit: {
            items: [{ type: 'ref', connection_key: 'cms', external_id: 'a1' }],
          },
        }),
      })
    )
  })

  it('uploads the manifest multipart with knobs and the CSRF header', async () => {
    const fetchMock = mockFetchJson({ campaign: { id: 'c1' } })
    global.fetch = fetchMock
    const file = new File(['{}'], 'manifest.jsonl')

    await createCampaignFromManifest('ws1', file, {
      name: '添加 · 开学季补录',
      watermark: 1000,
      batch_size: 500,
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/workspaces/ws1/campaigns/upload')
    expect(init.method).toBe('POST')
    // 不能手设 Content-Type：boundary 由 FormData 生成。
    expect(
      (init.headers as Record<string, string>)['Content-Type']
    ).toBeUndefined()
    expect(
      (init.headers as Record<string, string>)['x-agent-legion-request']
    ).toBe('1')
    const form = init.body as FormData
    expect(form.get('mode')).toBe('submit')
    expect(form.get('name')).toBe('添加 · 开学季补录')
    expect(form.get('watermark')).toBe('1000')
    expect(form.get('batch_size')).toBe('500')
    expect(form.get('manifest')).toBeInstanceOf(File)
  })

  it('omits the name form field when the name is blank', async () => {
    const fetchMock = mockFetchJson({ campaign: { id: 'c1' } })
    global.fetch = fetchMock

    await createCampaignFromManifest('ws1', new File(['{}'], 'm.jsonl'))

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.body as FormData).get('name')).toBeNull()
  })

  it('previews with the dry-run shape', async () => {
    const fetchMock = mockFetchJson({
      result: { mode: 'rerun', total_count: 1, eligible_count: 1 },
    })
    global.fetch = fetchMock

    await previewCampaign('ws1', {
      mode: 'upgrade',
      name: '',
      rerun: { from_failed_node: false, job_ids: ['j1'] },
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/campaigns/preview',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          mode: 'upgrade',
          name: '',
          rerun: { from_failed_node: false, job_ids: ['j1'] },
        }),
      })
    )
  })

  it('lists campaigns newest-first with the limit', async () => {
    const fetchMock = mockFetchJson({ campaigns: [] })
    global.fetch = fetchMock

    await fetchCampaigns('ws1', 50)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/campaigns?limit=50',
      expect.anything()
    )
  })

  it('fetches a single campaign detail', async () => {
    const fetchMock = mockFetchJson({ campaign: { id: 'c1' } })
    global.fetch = fetchMock

    await fetchCampaign('ws1', 'c1/x')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/campaigns/c1%2Fx',
      expect.anything()
    )
  })

  it.each([
    ['pause', pauseCampaign],
    ['resume', resumeCampaign],
    ['cancel', cancelCampaign],
  ] as const)('posts the %s transition', async (action, fn) => {
    const fetchMock = mockFetchJson({ campaign: { id: 'c1' } })
    global.fetch = fetchMock

    await fn('ws1', 'c1')

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/workspaces/ws1/campaigns/c1/${action}`,
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('surfaces the structured error detail on failure', async () => {
    const detail = 'Campaign is completed (terminal)'
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail }),
      text: () => Promise.resolve(JSON.stringify({ detail })),
    } as Response)

    await expect(pauseCampaign('ws1', 'c1')).rejects.toThrow(
      'Campaign is completed (terminal)'
    )
  })
})
