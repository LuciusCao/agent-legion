import { renderHook, waitFor } from '@testing-library/react'
import { act, createElement, type ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createTestQueryClient } from '../../../testing/testQueryClient'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useWorkflowStudio } from './useWorkflowStudio'

function queryClientWrapper({ children }: { children: ReactNode }) {
  return createElement(
    QueryClientProvider,
    { client: createTestQueryClient() },
    children
  )
}

const BASELINE_YAML = `key: demo
label: Demo Workflow
nodes:
  a:
    capability: cap_a
    execution:
      runtime: pi
      model: gpt-5.1
`

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
  definition_yaml: BASELINE_YAML,
}

// 运行通道保存生效后的新 active（canonical 重排 + 新 runtime 值）。
const FRESH_YAML = `label: Demo Workflow
key: demo
nodes:
  a:
    capability: cap_a
    execution:
      runtime: pi
      model: gpt-5.2
`

const freshActivePayload = {
  ...activeRevisionPayload,
  definition_yaml: FRESH_YAML,
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
  saveWorkflowRuntimeConfig: vi.fn(),
  rollbackWorkflowRuntimeConfig: vi.fn(),
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
  saveWorkflowRuntimeConfig: (...args: unknown[]) =>
    mocks.saveWorkflowRuntimeConfig(...args),
  rollbackWorkflowRuntimeConfig: (...args: unknown[]) =>
    mocks.rollbackWorkflowRuntimeConfig(...args),
}))

vi.mock('../../../api/agentCatalogApi', () => ({
  getAgentCatalog: (...args: unknown[]) => mocks.getAgentCatalog(...args),
}))

const emptyCompare = {
  valid: true,
  creates_revision: false,
  base_revision: null,
  draft_workflow: null,
  summary: {
    risk_level: 'none',
    node_changes: [],
    edge_changes: [],
    intake_changes: [],
    metadata_changes: [],
    risk_flags: [],
  },
  errors: [],
}

/**
 * r1 P2-4：运行通道保存的静默基线推进（useDraftBaselineSync 的
 * runtimeSavedRef）集成测试——仿 #666「发布即基线推进」场景（本文件
 * useWorkflowStudio.draft.test.ts:120+），钉两条不变量：
 * ①无结构编辑：草稿对齐新基线且不报「已保留草稿」；
 * ②有结构编辑：草稿保留且同样不误报外部变更。
 */
describe('useWorkflowStudio 运行通道基线推进（M48 r1 P2-4）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(activeRevisionPayload)
    mocks.fetchWorkflowRevisions.mockResolvedValue({
      revisions: [activeRevisionPayload.revision],
    })
    mocks.fetchWorkspaces.mockResolvedValue({ workspaces: [{ id: 'ws1' }] })
    mocks.getAgentCatalog.mockResolvedValue({ agents: [] })
    mocks.compareWorkflowDraft.mockResolvedValue(emptyCompare)
    mocks.validateWorkflowDraft.mockResolvedValue({ valid: true, errors: [] })
    mocks.fetchWorkflowDraft.mockResolvedValue({
      definition_yaml: null,
      updated_at: null,
      generation: 0,
    })
    mocks.putWorkflowDraft.mockResolvedValue({
      definition_yaml: BASELINE_YAML,
      updated_at: '2026-08-27T00:00:00+00:00',
      generation: 1,
    })
    mocks.saveWorkflowRuntimeConfig.mockResolvedValue({
      revision: activeRevisionPayload.revision,
      runtime_version: {
        version: 1,
        revision_id: activeRevisionPayload.revision.id,
        changed_keys: ['nodes.a.model'],
        created_by: 'user:1',
        created_at: '2026-10-21T10:00:00',
      },
    })
  })

  async function renderStudio() {
    const view = renderHook(() => useWorkflowStudio('ws1'), {
      wrapper: queryClientWrapper,
    })
    await waitFor(() => expect(view.result.current.loadState).toBe('ready'))
    return view
  }

  it('无结构编辑：草稿对齐新 canonical 基线，不报「已保留草稿」', async () => {
    const { result } = await renderStudio()
    // 首次装载后的 active 拉取切换到新 canonical 基线。
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(freshActivePayload)

    act(() =>
      result.current.setDefinitionYaml(
        BASELINE_YAML.replace('model: gpt-5.1', 'model: gpt-5.2')
      )
    )
    await act(async () => {
      await result.current.saveRuntimeConfig()
    })

    await waitFor(() => expect(result.current.definitionYaml).toBe(FRESH_YAML))
    expect(result.current.dirty).toBe(false)
    expect(result.current.hasPreservedDraft).toBe(false)
    expect(result.current.lastRuntimeSavedKeys).toEqual(['nodes.a.model'])
  })

  it('有结构编辑：草稿保留（结构 + 新 runtime 切片），同样不误报外部变更', async () => {
    const { result } = await renderStudio()
    mocks.fetchActiveWorkflowRevision.mockResolvedValue(freshActivePayload)

    act(() =>
      result.current.setDefinitionYaml(
        BASELINE_YAML.replace('model: gpt-5.1', 'model: gpt-5.2').replace(
          'label: Demo Workflow',
          'label: Demo Workflow（草稿）'
        )
      )
    )
    await act(async () => {
      await result.current.saveRuntimeConfig()
    })

    await waitFor(() =>
      expect(result.current.definitionYaml).toContain('（草稿）')
    )
    expect(result.current.definitionYaml).toContain('model: gpt-5.2')
    // 静默基线推进：草稿保留但不打「基线有更新」的外部变更标记。
    expect(result.current.hasPreservedDraft).toBe(false)
    expect(result.current.dirty).toBe(true)
  })
})
