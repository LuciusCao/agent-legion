import { afterEach, describe, expect, it, vi } from 'vitest'

import { completeMaterial, createRun, presignMaterial } from './materialsApi'

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

describe('materialsApi', () => {
  it('presigns a material upload', async () => {
    const payload = {
      material: { id: 'm1' },
      upload_url: 'https://s3.example/put',
      upload_expires_in_seconds: 900,
      deduplicated: false,
    }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await presignMaterial('ws 1', {
      filename: 'a.mp4',
      size_bytes: 12,
      content_type: 'video/mp4',
      content_hash: 'abc',
    })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/materials/presign',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          filename: 'a.mp4',
          size_bytes: 12,
          content_type: 'video/mp4',
          content_hash: 'abc',
        }),
      })
    )
  })

  it('completes a material upload', async () => {
    const payload = { material: { id: 'm1', status: 'ready' } }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await completeMaterial('ws1', 'm 1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/materials/m%201/complete',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('creates a run with material and ref items', async () => {
    const payload = { run: { id: 'r1' }, created_count: 2, jobs: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await createRun('ws1', {
      workflow_key: 'demo',
      items: [
        { type: 'material', material_id: 'm1' },
        { type: 'ref', connection_key: 'cms', external_id: 'q1' },
      ],
    })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          workflow_key: 'demo',
          items: [
            { type: 'material', material_id: 'm1' },
            { type: 'ref', connection_key: 'cms', external_id: 'q1' },
          ],
        }),
      })
    )
  })
})
