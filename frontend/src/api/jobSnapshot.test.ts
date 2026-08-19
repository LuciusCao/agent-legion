import { afterEach, describe, expect, it, vi } from 'vitest'

import { appendFilterParams, fetchJobsSnapshot } from './jobSnapshot'

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

describe('job snapshot api', () => {
  it('fetches a snapshot with the default limit', async () => {
    const payload = { jobs: [], next_cursor: null }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchJobsSnapshot('ws 1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/jobs/snapshot?limit=200',
      expect.anything()
    )
  })

  it('encodes limit, cursor and every filter param', async () => {
    const fetchMock = mockFetchJson({ jobs: [] })
    global.fetch = fetchMock

    await fetchJobsSnapshot('ws1', 50, 'cursor/1', {
      status: 'failed',
      search: 'q',
      workflow_version: 3,
      workflow_version_none: true,
      active_node_key: 'node-a',
      packed: 0,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/snapshot?limit=50&cursor=cursor%2F1&status=failed&search=q' +
        '&workflow_version=3&workflow_version_none=true&active_node_key=node-a&packed=0',
      expect.anything()
    )
  })
})

describe('appendFilterParams', () => {
  it('does nothing without a filter', () => {
    const params = new URLSearchParams()

    appendFilterParams(params)

    expect(params.toString()).toBe('')
  })

  it('keeps zero and false values meaningful', () => {
    const params = new URLSearchParams()

    appendFilterParams(params, {
      workflow_version: 0,
      packed: 0,
      workflow_version_none: false,
      paused: false,
    })

    expect(params.get('workflow_version')).toBe('0')
    expect(params.get('packed')).toBe('0')
    expect(params.get('paused')).toBe('false')
  })

  it('sets the none flag and node key only when present', () => {
    const params = new URLSearchParams()

    appendFilterParams(params, {
      workflow_version_none: true,
      active_node_key: 'node-a',
    })

    expect(params.get('workflow_version_none')).toBe('true')
    expect(params.get('active_node_key')).toBe('node-a')
    expect(params.has('workflow_version')).toBe(false)
  })
})
