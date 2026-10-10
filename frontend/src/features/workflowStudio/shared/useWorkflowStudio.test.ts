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
import { useAgentPublishRequest } from './useAgentPublishRequest'
import { useAgentPublishNoticeStore } from './agentPublishNoticeStore'
import type { StudioPublishRequestRecord } from '../../../api/studioPublishRequestApi'

const activeRevisionPayload = {
  revision: {
    id: 'ws1:demo:v1',
    workspace_id: 'ws1',
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
  fetchPendingPublishRequest: vi.fn(),
  confirmPublishRequest: vi.fn(),
  cancelPublishRequest: vi.fn(),
}

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
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

vi.mock('../../../api/studioPublishRequestApi', () => ({
  fetchPendingPublishRequest: (...args: unknown[]) =>
    mocks.fetchPendingPublishRequest(...args),
  confirmPublishRequest: (...args: unknown[]) =>
    mocks.confirmPublishRequest(...args),
  cancelPublishRequest: (...args: unknown[]) =>
    mocks.cancelPublishRequest(...args),
}))

function publishRequestRecord(
  overrides: Partial<StudioPublishRequestRecord> = {}
): StudioPublishRequestRecord {
  return {
    id: 'req-1',
    workspace_id: 'ws1',
    chat_session_id: 's1',
    status: 'pending',
    created_by: 'studio-agent:u1',
    result_revision_id: null,
    draft_hash: null,
    created_at: '2026-09-03T10:00:00Z',
    expires_at: '2026-09-03T10:10:00Z',
    resolved_at: null,
    claimed_at: null,
    ...overrides,
  }
}

// compare/DAG/空态/选择相关用例；草稿应用/revision 切换/持久化用例在姊妹
// 文件 useWorkflowStudio.draft.test.ts（测试文件体积纪律拆分）。
describe('useWorkflowStudio', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(activeRevisionPayload)
    mocks.fetchWorkflowRevisions.mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    })
    mocks.fetchWorkspaces.mockResolvedValue({
      workspaces: [{ id: 'ws1' }],
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
    mocks.fetchPendingPublishRequest.mockResolvedValue(null)
    useAgentPublishNoticeStore.setState({
      resolvedNotice: null,
      lastResolvedRequestId: null,
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

    // 画布数据源是草稿：编辑后的草稿含节点 a（modified 角标落在它上面）；
    // added 的 b 不在草稿里，仍以幽灵节点 + 幽灵边补入。
    act(() => {
      result.current.setDefinitionYaml(
        'key: demo\nlabel: Changed\nnodes:\n  a:\n    capability: cap_a\n'
      )
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
      'key: ws1\nlabel: ws1\nnodes:\n  _start:\n    type: start\n  intake:\n    type: code\n    capability: intake\n    after: [_start]\n'
    )
    // 空态模板草稿同样驱动画布：workflow 来自模板 YAML 解析（含 _start/intake）。
    expect(result.current.workflow?.nodes.map((node) => node.key)).toEqual([
      '_start',
      'intake',
    ])
    expect(result.current.dirty).toBe(false)
    expect(result.current.canSubmit).toBe(false)
  })

  it('compare 传输失败进 error 态，retryCompare 重新发起（轮 6 H4）', async () => {
    mocks.compareWorkflowDraft.mockRejectedValue(new Error('network down'))
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
      result.current.setDefinitionYaml('key: demo\nlabel: My Draft\n')
    })
    await act(async () => {
      vi.advanceTimersByTime(450)
    })
    await waitFor(() => expect(result.current.compareState).toBe('error'))
    const calls = mocks.compareWorkflowDraft.mock.calls.length

    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'info',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        risk_flags: [],
      },
      errors: [],
    })
    act(() => result.current.retryCompare())
    await act(async () => {
      vi.advanceTimersByTime(450)
    })
    await waitFor(() =>
      expect(mocks.compareWorkflowDraft.mock.calls.length).toBeGreaterThan(
        calls
      )
    )
    await waitFor(() => expect(result.current.compareState).toBe('ready'))
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
    // codex 轮 3 P2：canPublish 还要求当前 YAML 自动校验通过——推进过保存
    // debounce（800ms）让草稿落盘触发自动校验。
    await act(async () => {
      vi.advanceTimersByTime(850)
    })
    await waitFor(() =>
      expect(result.current.validationMessage).toBe('校验通过')
    )
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

    // 未做任何编辑：空基线下 compare 也要自动跑一次；模板节点来自草稿
    // 记录本身，compare 的 added 角标 + 幽灵样式标注它们未在基线中。
    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() =>
      expect(mocks.compareWorkflowDraft).toHaveBeenCalledWith('ws1', {
        definition_yaml:
          'key: ws1\nlabel: ws1\nnodes:\n  _start:\n    type: start\n  intake:\n    type: code\n    capability: intake\n    after: [_start]\n',
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

  it('agent publish confirm resets the draft to the canonical baseline instead of staying dirty (#1122)', async () => {
    // #1122 回归钉（dirty chip 永真）：复刻 AgentPublishRequestDialog 的
    // 确认管道——confirm 的 onConfirmed 回调把发布的草稿原文登记进
    // justPublishedRef（与手动 publishDraft 同一收尾机制）。修复前 confirm
    // 无登记通道：发布存库的 revision 是 canonical 重建 YAML（重排 key），
    // 紧随的基线变化被 baseline sync 误判为外部变更，preserveDirtyDraft
    // 永真，chip 卡在「有未发布变更」。发布后草稿必须 reset 到新基线且
    // dirty/hasPreservedDraft 都消退。
    const editedYaml = 'key: demo\nlabel: My Draft\n'
    const canonicalYaml = 'label: My Draft\nkey: demo\n'
    const v2Payload = {
      revision: {
        ...activeRevisionPayload.revision,
        id: 'ws1:demo:v2',
        version: 2,
        definition_hash: 'hash5678',
      },
      workflow: activeRevisionPayload.workflow,
      definition_yaml: canonicalYaml,
    }
    mocks.fetchPendingPublishRequest.mockResolvedValue(publishRequestRecord())
    mocks.confirmPublishRequest.mockResolvedValue(
      publishRequestRecord({
        status: 'confirmed',
        result_revision_id: 'ws1:demo:v2',
        resolved_at: '2026-09-03T10:02:00Z',
      })
    )
    // 对话框与栏顶的生产拓扑：同一 QueryClient 下 studio hook 与 agent
    // 发布请求 hook 并存。
    const { result } = renderHook(
      () => {
        const studio = useWorkflowStudio('ws1')
        const agentRequest = useAgentPublishRequest('ws1')
        return { studio, agentRequest }
      },
      { wrapper: queryClientWrapper }
    )
    await waitFor(() => expect(result.current.studio.loadState).toBe('ready'))
    await waitFor(() =>
      expect(result.current.agentRequest.pendingRequest?.id).toBe('req-1')
    )

    act(() => {
      result.current.studio.setDefinitionYaml(editedYaml)
    })
    expect(result.current.studio.dirty).toBe(true)

    // 确认成功：active revision 换成 canonical 重建的 v2（与画布原文
    // 纯文本不等）——这正是手动发布路径靠 markDraftPublished 消化、
    // agent 路径此前漏掉的形态。
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(v2Payload)
    mocks.fetchWorkflowRevisions.mockResolvedValue({
      revisions: [v2Payload.revision],
    })
    await act(async () => {
      await result.current.agentRequest.confirm(() =>
        result.current.studio.markDraftPublished(editedYaml)
      )
    })

    await waitFor(() =>
      expect(result.current.studio.definitionYaml).toBe(canonicalYaml)
    )
    expect(result.current.studio.dirty).toBe(false)
    expect(result.current.studio.hasPreservedDraft).toBe(false)
  })
})
