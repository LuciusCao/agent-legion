import { renderHook, waitFor } from '@testing-library/react'
import { act, createElement, type ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createTestQueryClient } from '../../testing/testQueryClient'

function queryClientWrapper({ children }: { children: ReactNode }) {
  return createElement(
    QueryClientProvider,
    { client: createTestQueryClient() },
    children
  )
}
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudio } from './useWorkflowStudio'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'

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
  fetchWorkspaces: vi.fn(),
  compareWorkflowDraft: vi.fn(),
  publishWorkflowDraft: vi.fn(),
  validateWorkflowDraft: vi.fn(),
  fetchWorkflowDraft: vi.fn(),
  putWorkflowDraft: vi.fn(),
  getExecutorCatalog: vi.fn(),
}

vi.mock('../../api', () => ({
  fetchActiveWorkflowRevision: (...args: unknown[]) =>
    mocks.fetchActiveWorkflowRevision(...args),
  fetchWorkflowRevisions: (...args: unknown[]) =>
    mocks.fetchWorkflowRevisions(...args),
  fetchWorkflowRevisionDetail: (...args: unknown[]) =>
    mocks.fetchWorkflowRevisionDetail(...args),
  fetchWorkspaces: (...args: unknown[]) => mocks.fetchWorkspaces(...args),
  compareWorkflowDraft: (...args: unknown[]) =>
    mocks.compareWorkflowDraft(...args),
  publishWorkflowDraft: (...args: unknown[]) =>
    mocks.publishWorkflowDraft(...args),
  validateWorkflowDraft: (...args: unknown[]) =>
    mocks.validateWorkflowDraft(...args),
  fetchWorkflowDraft: (...args: unknown[]) => mocks.fetchWorkflowDraft(...args),
  putWorkflowDraft: (...args: unknown[]) => mocks.putWorkflowDraft(...args),
}))

vi.mock('../../api/executorApi', () => ({
  getExecutorCatalog: (...args: unknown[]) => mocks.getExecutorCatalog(...args),
}))

describe('useWorkflowStudio', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(activeRevisionPayload)
    mocks.fetchWorkflowRevisions.mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    })
    mocks.fetchWorkspaces.mockResolvedValue({
      workspaces: [{ id: 'ws1', default_workflow_key: 'demo' }],
    })
    mocks.getExecutorCatalog.mockResolvedValue({ executors: [] })
    mocks.publishWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.fetchWorkflowDraft.mockResolvedValue({
      definition_yaml: null,
      updated_at: null,
    })
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: 'key: demo\n',
      updated_at: '2026-08-27T00:00:00+00:00',
    })
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

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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
        allow_missing_baseline: false,
      })
    )
    expect(result.current.compareState).toBe('ready')
    expect(result.current.compareSummary?.nodeChanges).toHaveLength(1)
  })

  it('merges compare node changes into the DAG as badges and ghost nodes', async () => {
    // 画布展示 active 基线（只有节点 a）：modified 打在基线节点上，
    // added 以幽灵节点 + 幽灵边补入。
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      creates_revision: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'warning',
        node_changes: [
          {
            type: 'modified',
            node_key: 'a',
            label: 'A',
            fields: ['label'],
            risk: 'info',
          },
          {
            type: 'added',
            node_key: 'b',
            label: 'B',
            fields: [],
            risk: 'info',
          },
        ],
        edge_changes: [
          {
            type: 'added',
            source: 'a',
            target: 'b',
            before_condition: null,
            after_condition: null,
            risk: 'info',
          },
        ],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    })

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() => {
      result.current.setDefinitionYaml('key: demo\nlabel: Changed\n')
    })
    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() => expect(result.current.compareState).toBe('ready'))
    const modified = result.current.nodes.find((node) => node.key === 'a')
    expect(modified).toMatchObject({ changeType: 'modified', ghost: false })
    const ghost = result.current.nodes.find((node) => node.key === 'b')
    expect(ghost).toMatchObject({
      label: 'B',
      changeType: 'added',
      ghost: true,
    })
    expect(result.current.edges).toContainEqual({
      from: 'a',
      to: 'b',
      ghost: true,
    })
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

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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
        allow_missing_baseline: false,
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
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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

  it('keeps an adopted historical revision when the server draft arrives late', async () => {
    // 服务端草稿 GET 在途时采用历史版本：采用算「用户碰过」，迟到的服务端
    // 草稿不得覆盖刚采用的内容。
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
    let resolveDraftQuery: (value: {
      definition_yaml: string | null
      updated_at: string | null
    }) => void = () => {}
    mocks.fetchWorkflowDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveDraftQuery = resolve
        })
    )
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    await act(async () => {
      await result.current.selectRevision('rev-old')
    })
    act(() => result.current.useViewedRevisionAsDraft())
    expect(result.current.definitionYaml).toContain('Restored')

    await act(async () => {
      resolveDraftQuery({
        definition_yaml: 'key: demo\nlabel: Late Server Draft\n',
        updated_at: '2026-08-27T01:02:03+00:00',
      })
      await Promise.resolve()
    })

    // 先确认迟到的服务端草稿真的送达（hydration 记录其 savedAt），再断言
    // 已采用的历史版本未被它覆盖。
    await waitFor(() =>
      expect(result.current.draftSave.savedAt).toBe('2026-08-27T01:02:03+00:00')
    )
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

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
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

  const notFoundError = () =>
    Object.assign(new Error('No active workflow revision'), { status: 404 })

  it('enters empty mode with a template draft when no active revision exists', async () => {
    mocks.fetchActiveWorkflowRevision.mockRejectedValue(notFoundError())
    mocks.fetchWorkflowRevisions.mockResolvedValue({ revisions: [] })

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })

    await waitFor(() => expect(result.current.loadState).toBe('empty'))
    expect(result.current.definitionYaml).toBe(
      'key: demo\nlabel: demo\nnodes:\n  _start:\n    type: start\n  intake:\n    capability: intake\n    after: [_start]\n'
    )
    expect(result.current.workflow).toBeNull()
    expect(result.current.dirty).toBe(false)
    expect(result.current.canSubmit).toBe(false)
  })

  it('compares against an empty baseline only in empty mode', async () => {
    mocks.fetchActiveWorkflowRevision.mockRejectedValue(notFoundError())
    mocks.fetchWorkflowRevisions.mockResolvedValue({ revisions: [] })
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      creates_revision: true,
      base_revision: null,
      draft_workflow: { key: 'demo', label: 'demo', version: 0 },
      summary: {
        risk_level: 'info',
        node_changes: [
          {
            type: 'added',
            node_key: 'start',
            label: 'start',
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

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('empty'))

    act(() => {
      result.current.setDefinitionYaml('key: demo\nlabel: My Draft\n')
    })
    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() =>
      expect(mocks.compareWorkflowDraft).toHaveBeenCalledWith('ws1', {
        definition_yaml: 'key: demo\nlabel: My Draft\n',
        allow_missing_baseline: true,
      })
    )
    expect(result.current.compareState).toBe('ready')
    expect(result.current.canPublish).toBe(true)
  })

  it('previews the template draft as ghost nodes in empty mode before any edit', async () => {
    mocks.fetchActiveWorkflowRevision.mockRejectedValue(notFoundError())
    mocks.fetchWorkflowRevisions.mockResolvedValue({ revisions: [] })
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      creates_revision: true,
      base_revision: null,
      draft_workflow: { key: 'demo', label: 'demo', version: 0 },
      summary: {
        risk_level: 'info',
        node_changes: [
          {
            type: 'added',
            node_key: '_start',
            label: '_start',
            fields: [],
            risk: 'info',
          },
          {
            type: 'added',
            node_key: 'intake',
            label: 'intake',
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

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('empty'))

    // 未做任何编辑：空基线下 compare 也要自动跑一次，把模板草稿（含
    // _start）以 ghost 节点呈现在画布上。
    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() =>
      expect(mocks.compareWorkflowDraft).toHaveBeenCalledWith('ws1', {
        definition_yaml:
          'key: demo\nlabel: demo\nnodes:\n  _start:\n    type: start\n  intake:\n    capability: intake\n    after: [_start]\n',
        allow_missing_baseline: true,
      })
    )
    await waitFor(() => expect(result.current.compareState).toBe('ready'))
    const ghostStart = result.current.nodes.find(
      (node) => node.key === '_start'
    )
    expect(ghostStart?.ghost).toBe(true)
    expect(ghostStart?.changeType).toBe('added')
    expect(result.current.nodes.map((node) => node.key)).toContain('intake')
  })

  it('clears the selection when the workspace changes', async () => {
    const { result, rerender } = renderHook(
      ({ ws }: { ws: string }) => useWorkflowStudio(ws),
      { wrapper: queryClientWrapper, initialProps: { ws: 'ws1' } }
    )
    await waitFor(() => expect(result.current.loadState).toBe('ready'))
    act(() => result.current.setSelectedNodeKey('a'))
    expect(result.current.selectedNodeKey).toBe('a')

    // async act 冲刷 ws2 的查询解析，避免 act 外交互告警。
    await act(async () => {
      rerender({ ws: 'ws2' })
    })

    await waitFor(() => expect(result.current.selectedNodeKey).toBeNull())
  })

  it('clears the selection when the selected node disappears from the canvas', async () => {
    mocks.fetchActiveWorkflowRevision.mockRejectedValue(notFoundError())
    mocks.fetchWorkflowRevisions.mockResolvedValue({ revisions: [] })
    const emptyCompare = (keys: string[]) => ({
      valid: true,
      creates_revision: true,
      base_revision: null,
      draft_workflow: { key: 'demo', label: 'demo', version: 0 },
      summary: {
        risk_level: 'info',
        node_changes: keys.map((key) => ({
          type: 'added',
          node_key: key,
          label: key,
          fields: [],
          risk: 'info',
        })),
        edge_changes: [],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    })
    mocks.compareWorkflowDraft.mockResolvedValue(
      emptyCompare(['_start', 'intake'])
    )
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('empty'))
    await act(async () => {
      vi.advanceTimersByTime(450)
    })
    await waitFor(() => expect(result.current.compareState).toBe('ready'))

    act(() => result.current.setSelectedNodeKey('intake'))
    expect(result.current.selectedNodeKey).toBe('intake')

    // 草稿编辑把 intake 移除：ghost 预览刷新后选择自动清除。
    mocks.compareWorkflowDraft.mockResolvedValue(emptyCompare(['_start']))
    act(() => {
      result.current.setDefinitionYaml(
        'key: demo\nlabel: demo\nnodes:\n  _start:\n    type: start\n'
      )
    })
    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() => expect(result.current.selectedNodeKey).toBeNull())
  })

  it('stays in error state when the active revision 404s for an unknown workspace', async () => {
    mocks.fetchActiveWorkflowRevision.mockRejectedValue(notFoundError())
    mocks.fetchWorkflowRevisions.mockResolvedValue({ revisions: [] })
    mocks.fetchWorkspaces.mockResolvedValue({ workspaces: [] })

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })

    await waitFor(() => expect(result.current.loadState).toBe('error'))
  })

  it('applies the server-persisted draft over the baseline on first load', async () => {
    mocks.fetchWorkflowDraft.mockResolvedValue({
      definition_yaml: 'key: demo\nlabel: Server Draft\n',
      updated_at: '2026-08-27T01:02:03+00:00',
    })
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })

    await waitFor(() =>
      expect(result.current.definitionYaml).toBe(
        'key: demo\nlabel: Server Draft\n'
      )
    )

    // 服务端草稿 ≠ 基线即 dirty；且装载本身不触发任何 PUT。
    expect(result.current.dirty).toBe(true)
    expect(result.current.draftSave).toEqual({
      status: 'idle',
      savedAt: '2026-08-27T01:02:03+00:00',
    })
    // async act 冲刷 react-query 的异步通知，避免 act 外交互告警。
    await act(async () => {
      vi.advanceTimersByTime(2000)
    })
    expect(mocks.putWorkflowDraft).not.toHaveBeenCalled()
  })

  it('autosaves draft edits to the server after the debounce', async () => {
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('ready'))
    await waitFor(() =>
      expect(result.current.definitionYaml).toBe(
        activeRevisionPayload.definition_yaml
      )
    )

    act(() => {
      result.current.setDefinitionYaml('key: demo\nlabel: Autosaved\n')
    })
    await act(async () => {
      vi.advanceTimersByTime(850)
    })

    expect(mocks.putWorkflowDraft).toHaveBeenCalledWith(
      'ws1',
      'key: demo\nlabel: Autosaved\n'
    )
    await waitFor(() => expect(result.current.draftSave.status).toBe('saved'))
  })

  it('preserves a dirty draft when the baseline changes externally', () => {
    const fetchDetail = vi.fn()
    const { result, rerender } = renderHook(
      ({ originalYaml }: { originalYaml: string }) =>
        useWorkflowStudioDraft('ws1', originalYaml, null, null, fetchDetail),
      { initialProps: { originalYaml: 'key: demo\nlabel: v1\n' } }
    )

    // 初始装载：草稿跟随基线。
    expect(result.current.draftYaml).toBe('key: demo\nlabel: v1\n')
    act(() => result.current.setDraftYaml('key: demo\nlabel: my edits\n'))
    expect(result.current.dirty).toBe(true)

    // 外部（他人/他 tab）发布使基线前进：用户草稿保留，打 preserved 标记。
    rerender({ originalYaml: 'key: demo\nlabel: v2\n' })

    expect(result.current.draftYaml).toBe('key: demo\nlabel: my edits\n')
    expect(result.current.hasPreservedDraft).toBe(true)
  })

  it('resets to the new baseline when the draft is clean or matches it', () => {
    const fetchDetail = vi.fn()
    const { result, rerender } = renderHook(
      ({ originalYaml }: { originalYaml: string }) =>
        useWorkflowStudioDraft('ws1', originalYaml, null, null, fetchDetail),
      { initialProps: { originalYaml: 'key: demo\nlabel: v1\n' } }
    )

    // 干净草稿：跟随新基线。
    rerender({ originalYaml: 'key: demo\nlabel: v2\n' })
    expect(result.current.draftYaml).toBe('key: demo\nlabel: v2\n')
    expect(result.current.hasPreservedDraft).toBe(false)

    // 自己 publish 成功：草稿与新基线一致，常规 reset 不误标 preserved。
    rerender({ originalYaml: 'key: demo\nlabel: v3\n' })
    act(() => result.current.setDraftYaml('key: demo\nlabel: v3\n'))
    rerender({ originalYaml: 'key: demo\nlabel: v3\n' })
    expect(result.current.hasPreservedDraft).toBe(false)
  })

  it('stays in error state for non-404 active revision failures', async () => {
    mocks.fetchActiveWorkflowRevision.mockRejectedValue(
      Object.assign(new Error('boom'), { status: 500 })
    )

    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })

    await waitFor(() => expect(result.current.loadState).toBe('error'))
  })
})
