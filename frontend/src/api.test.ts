import { describe, expect, it, vi } from 'vitest'

import {
  api,
  createJobBatch,
  createWorkspace,
  fetchJobArtifact,
  fetchJobDetail,
  updateWorkspace,
} from './api'

function mockFetch(response: { ok: boolean; status: number; text: string }) {
  return vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status,
    text: () => Promise.resolve(response.text),
    json: () => Promise.resolve(JSON.parse(response.text)),
  } as Response)
}

describe('api error handling', () => {
  it('throws detail from JSON error response', async () => {
    global.fetch = mockFetch({
      ok: false,
      status: 400,
      text: JSON.stringify({ detail: 'Bad request' }),
    })
    await expect(api('/test')).rejects.toThrow('Bad request')
  })

  it('throws message from JSON error response when detail is absent', async () => {
    global.fetch = mockFetch({
      ok: false,
      status: 500,
      text: JSON.stringify({ message: 'Server error' }),
    })
    await expect(api('/test')).rejects.toThrow('Server error')
  })

  it('throws HTTP status for JSON error without detail or message', async () => {
    global.fetch = mockFetch({
      ok: false,
      status: 403,
      text: JSON.stringify({ error: 'Forbidden' }),
    })
    await expect(api('/test')).rejects.toThrow('HTTP 403')
  })

  it('truncates HTML error to 200 chars', async () => {
    const html = '<html>'.repeat(100)
    global.fetch = mockFetch({ ok: false, status: 502, text: html })
    await expect(api('/test')).rejects.toThrow(
      `HTTP 502: ${html.slice(0, 200)}`
    )
  })
})

describe('workspace api', () => {
  it('patches workspace cms config', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          workspace: {
            id: 'math',
            name: 'Math',
            default_pipeline_key: 'question_content',
            default_entity: 'question',
            cms_config: { subject_id: '5' },
          },
        }),
    } as Response)
    global.fetch = fetchMock

    const workspace = await updateWorkspace('math', {
      cms_config: { subject_id: '5' },
    })

    expect(workspace.cms_config).toEqual({ subject_id: '5' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/math',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ cms_config: { subject_id: '5' } }),
      })
    )
  })

  it('patches workspace default_entity and intake_config', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          workspace: {
            id: 'math',
            name: 'Math',
            default_pipeline_key: 'question_content',
            default_entity: 'knowledge',
            intake_config: { enabled_modes: ['manual'] },
          },
        }),
    } as Response)
    global.fetch = fetchMock

    const workspace = await updateWorkspace('math', {
      default_entity: 'knowledge',
      intake_config: { enabled_modes: ['manual'] },
    })

    expect(workspace.default_entity).toBe('knowledge')
    expect(workspace.intake_config).toEqual({ enabled_modes: ['manual'] })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/math',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          default_entity: 'knowledge',
          intake_config: { enabled_modes: ['manual'] },
        }),
      })
    )
  })

  it('creates workspace with new entity and intake config fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          workspace: {
            id: 'physics',
            name: 'Physics',
            default_pipeline_key: 'question_content',
            default_entity: 'knowledge',
            cms_config: { subject_id: '3' },
            resource_config: { storage: 's3' },
            intake_config: { enabled_modes: ['manual', 'cms'] },
          },
        }),
    } as Response)
    global.fetch = fetchMock

    const workspace = await createWorkspace(
      'Physics',
      { subject_id: '3' },
      { storage: 's3' },
      'knowledge',
      { enabled_modes: ['manual', 'cms'] }
    )

    expect(workspace.default_entity).toBe('knowledge')
    expect(workspace.intake_config).toEqual({
      enabled_modes: ['manual', 'cms'],
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          name: 'Physics',
          cms_config: { subject_id: '3' },
          resource_config: { storage: 's3' },
          default_entity: 'knowledge',
          intake_config: { enabled_modes: ['manual', 'cms'] },
        }),
      })
    )
  })
})

describe('createJobBatch', () => {
  it('sends question_ids and empty knowledge_codes when inputField is question_ids', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          batch: {},
          created_count: 2,
          jobs: [],
        }),
    } as Response)
    global.fetch = fetchMock

    await createJobBatch({
      workspaceId: 'math',
      pipelineKey: 'question_content',
      sourceKind: 'question_ids',
      inputField: 'question_ids',
      values: ['q1', 'q2'],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/math/job-batches',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          pipeline_key: 'question_content',
          entity: 'question',
          source_kind: 'question_ids',
          question_ids: ['q1', 'q2'],
          knowledge_codes: [],
        }),
      })
    )
  })

  it('sends knowledge_codes and empty question_ids when inputField is knowledge_codes', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          batch: {},
          created_count: 1,
          jobs: [],
        }),
    } as Response)
    global.fetch = fetchMock

    await createJobBatch({
      workspaceId: 'math',
      sourceKind: 'knowledge_codes',
      inputField: 'knowledge_codes',
      values: ['k1'],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/math/job-batches',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          pipeline_key: 'question_content',
          entity: 'question',
          source_kind: 'knowledge_codes',
          knowledge_codes: ['k1'],
          question_ids: [],
        }),
      })
    )
  })
})

describe('job helpers', () => {
  it('fetchJobDetail calls correct endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          job: {
            id: 'j1',
            workspace_id: 'w1',
            pipeline_key: 'p1',
            source_id: 's1',
            title: 'T',
            status: 'running',
          },
          nodes: [],
          runs: [],
          artifacts: [],
        }),
    } as Response)
    global.fetch = fetchMock

    const result = await fetchJobDetail('j1')
    expect(result.job.id).toBe('j1')
    expect(fetchMock).toHaveBeenCalledWith('/api/jobs/j1', expect.any(Object))
  })

  it('fetchJobArtifact calls correct endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ name: 'log.txt', content: 'hello' }),
    } as Response)
    global.fetch = fetchMock

    const result = await fetchJobArtifact('j1', 'log.txt')
    expect(result.name).toBe('log.txt')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs/j1/artifacts/log.txt',
      expect.any(Object)
    )
  })
})
