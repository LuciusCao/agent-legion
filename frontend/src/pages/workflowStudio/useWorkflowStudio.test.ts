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
  compareWorkflowDraft: vi.fn(),
  publishWorkflowDraft: vi.fn(),
  validateWorkflowDraft: vi.fn(),
}

vi.mock('../../api', () => ({
  fetchActiveWorkflowRevision: (...args: unknown[]) =>
    mocks.fetchActiveWorkflowRevision(...args),
  fetchWorkflowRevisions: (...args: unknown[]) =>
    mocks.fetchWorkflowRevisions(...args),
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
        metadata_changes: [],
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
        metadata_changes: [],
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
        metadata_changes: [],
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

  it('enables publish when compare returns metadata changes', async () => {
    mocks.compareWorkflowDraft.mockResolvedValue({
      valid: true,
      base_revision: null,
      draft_workflow: null,
      summary: {
        risk_level: 'info',
        node_changes: [],
        edge_changes: [],
        intake_changes: [],
        metadata_changes: [
          {
            type: 'modified',
            field: 'label',
            before_value: 'Demo Workflow',
            after_value: 'Demo Workflow v2',
            risk: 'info',
          },
        ],
        risk_flags: [],
      },
      errors: [],
    })

    const { result } = renderHook(() => useWorkflowStudio('ws1'))
    await waitFor(() => expect(result.current.loadState).toBe('ready'))

    act(() => {
      result.current.setDefinitionYaml('key: demo\nlabel: Demo Workflow v2\n')
    })

    await act(async () => {
      vi.advanceTimersByTime(450)
    })

    await waitFor(() => expect(result.current.compareState).toBe('ready'))
    expect(result.current.compareSummary?.metadataChanges).toHaveLength(1)
    expect(result.current.canPublish).toBe(true)
  })
})
