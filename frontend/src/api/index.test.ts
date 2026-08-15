/// <reference types="node" />
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  api,
  createJobBatch,
  createWorkspace,
  deleteWorkspacePackage,
  fetchActiveWorkflowRevision,
  fetchJobArtifact,
  fetchJobDetail,
  fetchWorkflowRevisionDetail,
  fetchWorkflowRevisions,
  fetchWorkspacePackages,
  updateWorkspace,
  updateWorkspacePackage,
} from './index'
import {
  getExecutorCatalog,
  getSkillDetail,
  getWorkspaceExecutorConfiguration,
} from './executorApi'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

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
  it('patches workspace resource config', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          workspace: {
            id: 'math',
            name: 'Math',
            default_workflow_key: 'demo_workflow',
            default_entity: 'question',
            resource_config: {
              resources: { question_detail: { enabled: true, config: {} } },
            },
          },
        }),
    } as Response)
    global.fetch = fetchMock

    const workspace = await updateWorkspace('math', {
      resource_config: {
        resources: { question_detail: { enabled: true, config: {} } },
      },
    })

    expect(workspace.resource_config).toEqual({
      resources: { question_detail: { enabled: true, config: {} } },
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/math',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          resource_config: {
            resources: { question_detail: { enabled: true, config: {} } },
          },
        }),
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
            default_workflow_key: 'demo_workflow',
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
            default_workflow_key: 'demo_workflow',
            default_entity: 'knowledge',
            resource_config: { storage: 's3' },
            intake_config: { enabled_modes: ['manual', 'cms'] },
          },
        }),
    } as Response)
    global.fetch = fetchMock

    const workspace = await createWorkspace(
      'Physics',
      'demo_workflow',
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
          default_workflow_key: 'demo_workflow',
          resource_config: { storage: 's3' },
          default_entity: 'knowledge',
          intake_config: { enabled_modes: ['manual', 'cms'] },
        }),
      })
    )
  })
})

describe('createJobBatch', () => {
  it('posts the batch request body to the workspace endpoint', async () => {
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

    await createJobBatch('math', {
      async_processing: false,
      workflow_key: 'demo_workflow',
      source_kind: 'question_ids',
      question_ids: ['q1', 'q2'],
      knowledge_codes: [],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/math/job-batches',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          async_processing: false,
          workflow_key: 'demo_workflow',
          source_kind: 'question_ids',
          question_ids: ['q1', 'q2'],
          knowledge_codes: [],
        }),
      })
    )
  })

  it('forwards an explicit async_processing flag', async () => {
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

    await createJobBatch('math', {
      async_processing: true,
      workflow_key: 'demo_video_workflow',
      source_kind: 'knowledge_codes',
      knowledge_codes: ['k1'],
      question_ids: [],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/math/job-batches',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          async_processing: true,
          workflow_key: 'demo_video_workflow',
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
            workflow_key: 'p1',
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

describe('executor configuration api', () => {
  function mockFetchJson(response: unknown) {
    return vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    } as Response)
  }

  it('loads normalized executor catalog', async () => {
    const fetchMock = mockFetchJson({ executors: [] })
    global.fetch = fetchMock

    await getExecutorCatalog()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/executors',
      expect.objectContaining({ cache: 'no-store' })
    )
  })

  it('loads workspace executor configuration', async () => {
    const fetchMock = mockFetchJson({
      allocations: [],
      bindings: [],
      node_limits: [],
      migration_warnings: [],
    })
    global.fetch = fetchMock

    await getWorkspaceExecutorConfiguration('reading team')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/reading%20team/executor-configuration',
      expect.objectContaining({ cache: 'no-store' })
    )
  })

  it('loads a configured skill detail', async () => {
    const fetchMock = mockFetchJson({ key: 'demo/review', files: [] })
    global.fetch = fetchMock

    await getSkillDetail('demo/review')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/executors/skills/demo/review',
      expect.objectContaining({ cache: 'no-store' })
    )
  })
})

describe('workspace packages api', () => {
  function mockFetchJson(response: unknown) {
    return vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(response),
    } as Response)
  }

  it('fetches workspace package collections', async () => {
    const fetchMock = mockFetchJson({ packages: [] })
    global.fetch = fetchMock

    await fetchWorkspacePackages('workspace/one')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/workspace%2Fone/packages',
      expect.any(Object)
    )
  })

  it('deletes workspace packages', async () => {
    const fetchMock = mockFetchJson({ deleted: true })
    global.fetch = fetchMock

    await deleteWorkspacePackage('workspace/one', 34)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/workspace%2Fone/packages/34',
      expect.objectContaining({ method: 'DELETE' })
    )
  })

  it('patches workspace packages', async () => {
    const fetchMock = mockFetchJson({ id: 34 })
    global.fetch = fetchMock

    await updateWorkspacePackage('workspace/one', 34, { locked: false })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/workspace%2Fone/packages/34',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ locked: false }),
      })
    )
  })
})

describe('workflow revisions api', () => {
  it('fetches the active workspace workflow revision', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          revision: {
            id: 'ws1:demo_workflow:v1',
            workspace_id: 'ws1',
            workflow_key: 'demo_workflow',
            version: 1,
            status: 'active',
            definition_hash: 'abcdef123456',
            created_at: '2026-07-02T00:00:00Z',
            published_at: '2026-07-02T00:00:00Z',
          },
          workflow: {
            key: 'demo_workflow',
            label: '题目审题信息生成 DAG',
            intake: { modes: [] },
            nodes: [],
            edges: [],
          },
          definition_yaml: 'key: demo_workflow\n',
        }),
    } as Response)
    global.fetch = fetchMock

    const result = await fetchActiveWorkflowRevision('ws1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/workflow-revisions/active',
      expect.any(Object)
    )
    expect(result.revision.version).toBe(1)
    expect(result.definition_yaml).toContain('demo_workflow')
  })

  it('fetches workspace workflow revisions', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          revisions: [
            {
              id: 'ws1:demo_workflow:v1',
              workspace_id: 'ws1',
              workflow_key: 'demo_workflow',
              version: 1,
              status: 'active',
              definition_hash: 'abcdef123456',
              created_at: '2026-07-02T00:00:00Z',
              published_at: '2026-07-02T00:00:00Z',
            },
          ],
        }),
    } as Response)
    global.fetch = fetchMock

    const result = await fetchWorkflowRevisions('ws1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/workflow-revisions',
      expect.any(Object)
    )
    expect(result.revisions[0].status).toBe('active')
  })

  it('fetches a workspace workflow revision detail', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          revision: {
            id: 'rev-1',
            workspace_id: 'ws1',
            workflow_key: 'demo_video_workflow',
            version: 1,
            status: 'archived',
            definition_hash: '17d8077e',
            created_at: '2026-07-06T10:00:00Z',
            published_at: '2026-07-06T10:05:00Z',
          },
          workflow: {
            key: 'demo_video_workflow',
            label: '知识视频 DAG',
            schema_version: 2,
            intake: { modes: {} },
            nodes: [],
            edges: [],
          },
          definition_yaml: 'key: demo_video_workflow\nlabel: 知识视频 DAG\n',
        }),
    } as Response)
    global.fetch = fetchMock

    const result = await fetchWorkflowRevisionDetail('ws1', 'rev-1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/workflow-revisions/rev-1',
      expect.any(Object)
    )
    expect(result.revision.id).toBe('rev-1')
    expect(result.workflow.key).toBe('demo_video_workflow')
    expect(result.definition_yaml).toContain('key: demo_video_workflow')
  })
})
