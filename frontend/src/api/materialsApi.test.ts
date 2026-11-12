import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { completeMaterial, createRun, presignMaterial } from './materialsApi'
import {
  DEFAULT_WRITE_TIMEOUT_MS,
  REQUEST_TIMEOUT_CODE,
} from './requestTimeout'

const originalFetch = global.fetch

/** 模拟僵死后端（同 coreTimeout.test.ts 的形态）：永不响应，只在 signal
 * abort 时以 AbortError 拒绝。 */
function hangingFetch() {
  return vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (signal?.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
        return
      }
      signal?.addEventListener('abort', () => {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
      })
    })
  })
}

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

  it('completes with the expected upload generation in the body (#1282 P1)', async () => {
    const payload = { material: { id: 'm1', status: 'ready' } }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    await completeMaterial('ws1', 'm1', 2)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/materials/m1/complete',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ expected_upload_generation: 2 }),
      })
    )
  })

  it('creates a run with material and ref items', async () => {
    const payload = { run: { id: 'r1' }, created_count: 2 }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await createRun('ws1', {
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
          items: [
            { type: 'material', material_id: 'm1' },
            { type: 'ref', connection_key: 'cms', external_id: 'q1' },
          ],
        }),
      })
    )
  })

  describe('bulk submit timeout exemption (#1157 phase0)', () => {
    const manyItems = (count: number) =>
      Array.from({ length: count }, (_, i) => ({
        type: 'material' as const,
        material_id: `m${i}`,
      }))

    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('exempts submissions over 1000 items from the write timeout', async () => {
      global.fetch = hangingFetch()
      let settled = false
      void createRun('ws1', { items: manyItems(1001) }).catch(() => {
        settled = true
      })
      // 豁免后 10 倍写超时也不 abort（服务端分钟级建行不再被误报失败）。
      await vi.advanceTimersByTimeAsync(DEFAULT_WRITE_TIMEOUT_MS * 10)
      expect(settled).toBe(false)
    })

    it('keeps the 120s write timeout at exactly 1000 items', async () => {
      global.fetch = hangingFetch()
      const pending = createRun('ws1', { items: manyItems(1000) }).catch(
        (err: unknown) => err
      )
      await vi.advanceTimersByTimeAsync(DEFAULT_WRITE_TIMEOUT_MS - 1)
      await vi.advanceTimersByTimeAsync(1)
      expect(await pending).toMatchObject({
        code: REQUEST_TIMEOUT_CODE,
        timeoutMs: DEFAULT_WRITE_TIMEOUT_MS,
      })
    })
  })
})
