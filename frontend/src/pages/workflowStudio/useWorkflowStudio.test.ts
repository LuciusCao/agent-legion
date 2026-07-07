import { renderHook, waitFor } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudio } from './useWorkflowStudio'

const activeRevisionPayload = {
  revision: {
    id: 'ws1:demo:v1',
    workspace_id: 'ws1',
    workflow_key: 'demo',
    version: 1,
    status: 'active',
    definition_hash: 'hash1234',
    created_at: '2026-07-02T00:00:00Z',
    published_at: '2026-07-02T00:00:00Z',
  },
  workflow: {
    key: 'demo',
    label: 'Demo Workflow',
    intake: { modes: [] },
    nodes: [
      {
        key: 'a',
        label: 'A',
        capability: 'cap_a',
        after: [],
        inputs: [],
        outputs: [],
      },
    ],
    edges: [],
  },
  definition_yaml: 'key: demo\nlabel: Demo Workflow\n',
}

const mocks = {
  fetchActiveWorkflowRevision: vi.fn(),
  fetchWorkflowRevisions: vi.fn(),
  fetchWorkflowRevisionDetail: vi.fn(),
  compareWorkflowDraft: vi.fn(),
  publishWorkflowDraft: vi.fn(),
  validateWorkflowDraft: vi.fn(),
}

vi.mock('../../api', () => ({
  fetchActiveWorkflowRevision: (...args: unknown[]) =>
    mocks.fetchActiveWorkflowRevision(...args),
  fetchWorkflowRevisions: (...args: unknown[]) =>
    mocks.fetchWorkflowRevisions(...args),
  fetchWorkflowRevisionDetail: (...args: unknown[]) =>
    mocks.fetchWorkflowRevisionDetail(...args),
  compareWorkflowDraft: (...args: unknown[]) =>
    mocks.compareWorkflowDraft(...args),
  publishWorkflowDraft: (...args: unknown[]) =>
    mocks.publishWorkflowDraft(...args),
  validateWorkflowDraft: (...args: unknown[]) =>
    mocks.validateWorkflowDraft(...args),
}))

describe('useWorkflowStudio', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(activeRevisionPayload)
    mocks.fetchWorkflowRevisions.mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    })
    mocks.publishWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
  })

  it('does not call compare for unchanged draft', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'none',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    })

    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))
    await waitFor(() =>
      expect(result.current.definitionYaml).toBe(
        activeRevisionPayload.definition_yaml
      )
    )

    act(() => {
      vi.advanceTimersByTime(500)
    })

    expect(mocks.compareWorkflowDraft).not.toHaveBeenCalled()
    expect(result.current.compareState).toBe('idle')
  })

  it('calls compare after draft change with debounce', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'info',
        node_changes: [
          {
            type: 'added',
            node_key: 'b',
            label: 'B',
            fields: [],
            risk: 'info',
          },
        ],
        edge_changes: [],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    })

    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() => {
      result.current.setDefinitionYaml('key: demo\nlabel: Changed\n')
    })

    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() =>
      expect(mocks.compareWorkflowDraft).toHaveBeenCalledWith('ws1', {
        definition_yaml: 'key: demo\nlabel: Changed\n',
      })
    )
    expect(result.current.compareState).toBe('ready')
    expect(result.current.compareSummary?.nodeChanges).toHaveLength(1)
  })

  it('disables publish when compare result is invalid', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: false,
      base_revision: null,
      draft_workflow: null,
      summary: null,
      errors: [
        {
          category: 'yaml',
          message: "could not find expected ':'",
        },
      ],
    })

    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() => {
      result.current.setDefinitionYaml('invalid yaml')
    })

    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() => expect(result.current.compareState).toBe('ready'))
    expect(result.current.canPublish).toBe(false)
  })

  it('updates stale compare cleanly when new draft replaces old one', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'info',
        node_changes: [
          {
            type: 'added',
            node_key: 'b',
            label: 'B',
            fields: [],
            risk: 'info',
          },
        ],
        edge_changes: [],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    })

    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() => {
      result.current.setDefinitionYaml('key: demo\nlabel: Draft 1\n')
    })

    act(() => {
      result.current.setDefinitionYaml('key: demo\nlabel: Draft 2\n')
    })

    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() =>
      expect(mocks.compareWorkflowDraft).toHaveBeenLastCalledWith('ws1', {
        definition_yaml: 'key: demo\nlabel: Draft 2\n',
      })
    )
    expect(result.current.compareSummary?.nodeChanges[0]?.nodeKey).toBe('b')
  })

  it('loads a historical revision without replacing a dirty draft', async () => {
    mocks.fetchWorkflowRevisionDetail.mockResolvedValue({
      revision: {
        id: 'rev-old',
        workspace_id: 'ws1',
        workflow_key: 'wf',
        version: 1,
        status: 'archived',
        definition_hash: 'oldhash',
        created_at: '2026-07-05T10:00:00Z',
        published_at: '2026-07-05T10:05:00Z',
      },
      workflow: {
        ...activeRevisionPayload.workflow,
        label: 'Old Workflow',
      },
      definition_yaml:
        'key: wf\nlabel: Old Workflow\nschema_version: 2\nnodes: {}\nedges: []\n',
    })
    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() =>
      result.current.setDefinitionYaml(
        `${result.current.definitionYaml}\n# draft`
      )
    )
    await act(async () => {
      await result.current.selectRevision('rev-old')
    })

    expect(result.current.viewMode).toBe('revision')
    expect(result.current.readOnly).toBe(true)
    expect(result.current.hasPreservedDraft).toBe(true)
    expect(result.current.definitionYaml).toContain('Old Workflow')
    expect(result.current.canPublish).toBe(false)

    act(() => result.current.backToDraft())

    expect(result.current.viewMode).toBe('draft')
    expect(result.current.definitionYaml).toContain('# draft')
  })

  it('uses a historical revision as a new draft', async () => {
    mocks.fetchWorkflowRevisionDetail.mockResolvedValue({
      revision: {
        id: 'rev-old',
        workspace_id: 'ws1',
        workflow_key: 'wf',
        version: 1,
        status: 'archived',
        definition_hash: 'oldhash',
        created_at: '2026-07-05T10:00:00Z',
        published_at: '2026-07-05T10:05:00Z',
      },
      workflow: activeRevisionPayload.workflow,
      definition_yaml:
        'key: wf\nlabel: Restored\nschema_version: 2\nnodes: {}\nedges: []\n',
    })
    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    await act(async () => {
      await result.current.selectRevision('rev-old')
    })
    act(() => result.current.useViewedRevisionAsDraft())

    expect(result.current.viewMode).toBe('draft')
    expect(result.current.readOnly).toBe(false)
    expect(result.current.definitionYaml).toContain('Restored')
    expect(result.current.dirty).toBe(true)
  })

  it('ignores stale revision detail when a newer revision is requested', async () => {
    const slowPayload = {
      revision: {
        id: 'rev-slow',
        workspace_id: 'ws1',
        workflow_key: 'wf',
        version: 1,
        status: 'archived',
        definition_hash: 'slowhash',
        created_at: '2026-07-05T10:00:00Z',
        published_at: '2026-07-05T10:05:00Z',
      },
      workflow: activeRevisionPayload.workflow,
      definition_yaml: 'key: wf\nlabel: Slow\n',
    }
    const fastPayload = {
      revision: {
        id: 'rev-fast',
        workspace_id: 'ws1',
        workflow_key: 'wf',
        version: 2,
        status: 'archived',
        definition_hash: 'fasthash',
        created_at: '2026-07-05T11:00:00Z',
        published_at: '2026-07-05T11:05:00Z',
      },
      workflow: activeRevisionPayload.workflow,
      definition_yaml: 'key: wf\nlabel: Fast\n',
    }

    let resolveSlow: (value: typeof slowPayload) => void = () => {}
    let resolveFast: (value: typeof fastPayload) => void = () => {}

    mocks.fetchWorkflowRevisionDetail.mockImplementation(
      (revisionId: string) => {
        if (revisionId === 'rev-slow') {
          return new Promise<typeof slowPayload>((resolve) => {
            resolveSlow = resolve
          })
        }
        return new Promise<typeof fastPayload>((resolve) => {
          resolveFast = resolve
        })
      }
    )

    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() => {
      result.current.selectRevision('rev-slow')
      result.current.selectRevision('rev-fast')
    })

    act(() => resolveFast(fastPayload))
    await waitFor(() => expect(result.current.definitionYaml).toContain('Fast'))
    expect(result.current.selectedRevisionId).toBe('rev-fast')

    act(() => resolveSlow(slowPayload))
    await waitFor(() => expect(result.current.definitionYaml).toContain('Fast'))
    expect(result.current.selectedRevisionId).toBe('rev-fast')
  })

  it('exposes revision load error and keeps previous view on failure', async () => {
    mocks.fetchWorkflowRevisionDetail.mockRejectedValue(
      new Error('network error')
    )
    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))
    const previousDefinitionYaml = result.current.definitionYaml

    await act(async () => {
      await result.current.selectRevision('rev-old')
    })

    expect(result.current.isLoadingRevision).toBe(false)
    expect(result.current.revisionLoadError).toBe('network error')
    expect(result.current.viewMode).toBe('draft')
    expect(result.current.definitionYaml).toBe(previousDefinitionYaml)
  })
})
