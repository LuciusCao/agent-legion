import { renderHook, waitFor } from '@testing-library/react'
import { act, createElement, type ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createTestQueryClient } from '../../../testing/testQueryClient'

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
  definition_yaml:
    'key: demo\nlabel: Demo Workflow\nnodes:\n  a:\n    capability: cap_a\n',
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
  getAgentCatalog: vi.fn(),
}

vi.mock('../../../api', () => ({
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

vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: (...args: unknown[]) => mocks.getAgentCatalog(...args),
}))

const archivedRevisionDetail = {
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
}

const emptyCompareSummary = {
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
}

// 草稿应用/revision 切换/持久化/画布数据源用例（自 useWorkflowStudio.test.ts
// 按测试文件体积纪律拆出；compare/DAG/空态用例留在原文件）。
describe('useWorkflowStudio draft & revision', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(activeRevisionPayload)
    mocks.fetchWorkflowRevisions.mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    })
    mocks.fetchWorkspaces.mockResolvedValue({
      workspaces: [{ id: 'ws1', default_workflow_key: 'demo' }],
    })
    mocks.getAgentCatalog.mockResolvedValue({ agents: [] })
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

  it('loads a historical revision without replacing a dirty draft', async () => {
    mocks.fetchWorkflowRevisionDetail.mockResolvedValue({
      ...archivedRevisionDetail,
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
      ...archivedRevisionDetail,
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
      ...archivedRevisionDetail,
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
    })
    act(() => {
      result.current.selectRevision('rev-fast')
    })

    await act(async () => {
      resolveFast(fastPayload)
      await Promise.resolve()
    })
    await waitFor(() => expect(result.current.viewMode).toBe('revision'))
    expect(result.current.definitionYaml).toContain('Fast')

    await act(async () => {
      resolveSlow(slowPayload)
      await Promise.resolve()
    })
    expect(result.current.definitionYaml).toContain('Fast')
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

  it('renders the draft rather than the published workflow on the canvas', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue(emptyCompareSummary)
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
      result.current.setDefinitionYaml(
        'key: demo\nlabel: Draft\nnodes:\n  a:\n    capability: cap_a\n  b:\n    capability: cap_b\n    after: [a]\nedges:\n  - from: a\n    to: b\n'
      )
    })

    // 已发布 workflow 只有节点 a；画布数据源立即跟随草稿（含新增的 b），
    // 边按持久化格式 from/to 映射（source/target/condition 是错误字段名）。
    expect(result.current.workflow?.nodes.map((node) => node.key)).toEqual([
      'a',
      'b',
    ])
    expect(result.current.nodes.map((node) => node.key)).toContain('b')
    expect(result.current.edges).toContainEqual({
      from: 'a',
      to: 'b',
      label: '',
      conditional: false,
    })
  })

  it('falls back to the published workflow while the draft YAML is invalid mid-edit', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue(emptyCompareSummary)
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() => {
      result.current.setDefinitionYaml('key: demo\nnodes: [broken')
    })

    // 编辑中途 YAML 非法：画布回退已发布版本（不报错、不清空），编辑恢复
    // 合法后重新跟随草稿。
    expect(result.current.workflow?.label).toBe('Demo Workflow')
    expect(result.current.workflow?.nodes.map((node) => node.key)).toEqual([
      'a',
    ])
    act(() => {
      result.current.setDefinitionYaml(
        'key: demo\nlabel: Draft\nnodes:\n  a:\n    capability: cap_a\n  b:\n    capability: cap_b\n'
      )
    })
    expect(result.current.workflow?.nodes.map((node) => node.key)).toEqual([
      'a',
      'b',
    ])
  })

  it('falls back to the published workflow for structurally malformed draft YAML', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue(emptyCompareSummary)
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    // `nodes:\n  review:`（值为 null）：语法合法但形状残缺，不得在渲染期
    // 抛异常 crash Studio，回退 published 画布（警示 chip 由组件层覆盖）。
    act(() => {
      result.current.setDefinitionYaml('key: demo\nnodes:\n  review:\n')
    })

    expect(result.current.workflow?.label).toBe('Demo Workflow')
    expect(result.current.workflow?.nodes.map((node) => node.key)).toEqual([
      'a',
    ])
  })

  it('renders the viewed revision in revision mode and restores the draft canvas on return', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue(emptyCompareSummary)
    mocks.fetchWorkflowRevisionDetail.mockResolvedValue({
      ...archivedRevisionDetail,
      workflow: {
        ...activeRevisionPayload.workflow,
        label: 'Old Workflow',
      },
      definition_yaml: 'key: wf\nlabel: Old Workflow\n',
    })
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('ready'))
    act(() => {
      result.current.setDefinitionYaml(
        'key: demo\nlabel: Draft\nnodes:\n  a:\n    capability: cap_a\n  b:\n    capability: cap_b\n'
      )
    })

    await act(async () => {
      await result.current.selectRevision('rev-old')
    })
    expect(result.current.viewMode).toBe('revision')
    expect(result.current.workflow?.label).toBe('Old Workflow')

    // backToDraft 一键回来就是离开时的草稿，画布随之切回草稿记录。
    act(() => result.current.backToDraft())
    expect(result.current.viewMode).toBe('draft')
    expect(result.current.workflow?.nodes.map((node) => node.key)).toEqual([
      'a',
      'b',
    ])
  })

  it('keeps the compare summary available while viewing a revision with a dirty draft', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue({
      ...emptyCompareSummary,
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
    })
    mocks.fetchWorkflowRevisionDetail.mockResolvedValue({
      ...archivedRevisionDetail,
      workflow: activeRevisionPayload.workflow,
      definition_yaml: 'key: wf\nlabel: Old\n',
    })
    const { result } = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(result.current.loadState).toBe('ready'))
    act(() => {
      result.current.setDefinitionYaml(
        'key: demo\nlabel: Draft\nnodes:\n  a:\n    capability: cap_a\n  b:\n    capability: cap_b\n'
      )
    })
    await act(async () => {
      vi.advanceTimersByTime(450)
    })
    await waitFor(() => expect(result.current.compareState).toBe('ready'))

    await act(async () => {
      await result.current.selectRevision('rev-old')
    })

    // 「草稿有未发布更改」的数据源在 revision 模式保持可用（顶栏 chip 据此
    // 持续提示）；dirty 本身仍按 viewMode 门控，且草稿 diff 不叠加到被查看
    // revision 的画布上。
    expect(result.current.dirty).toBe(false)
    expect(result.current.compareSummary?.nodeChanges).toHaveLength(1)
    expect(
      result.current.nodes.every((node) => node.changeType === undefined)
    ).toBe(true)
  })
})
