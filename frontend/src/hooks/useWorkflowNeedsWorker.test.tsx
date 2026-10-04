import type { ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createTestQueryClient } from '../testing/testQueryClient'
import type { WorkflowDefinitionRecord } from '../types'
import { useWorkflowNeedsWorker } from './useWorkflowNeedsWorker'

const mocks = vi.hoisted(() => ({ fetchConsole: vi.fn() }))
vi.mock('../api/agentWorkers', () => ({
  fetchWorkerConsole: mocks.fetchConsole,
}))

function workflow(...nodeTypes: string[]) {
  return {
    key: 'wf',
    label: 'wf',
    nodes: nodeTypes.map((node_type, index) => ({
      key: `n${index}`,
      node_type,
    })),
  } as unknown as WorkflowDefinitionRecord
}

function mount(
  definition: WorkflowDefinitionRecord | null,
  options: { enabled?: boolean; whenNoWorkflow: boolean }
) {
  const client = createTestQueryClient()
  return renderHook(() => useWorkflowNeedsWorker(definition, options), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
}

describe('useWorkflowNeedsWorker', () => {
  beforeEach(() => {
    mocks.fetchConsole.mockReset()
  })

  it('needs a Worker for code-only workflows on a pure-remote instance', async () => {
    mocks.fetchConsole.mockResolvedValue({
      console_url: '',
      code_requires_worker: true,
    })
    const { result } = mount(workflow('start', 'code'), {
      whenNoWorkflow: false,
    })
    await waitFor(() =>
      expect(result.current).toEqual({ agent: false, code: true })
    )
  })

  it('keeps code-only workflows Host-local on a default instance', async () => {
    mocks.fetchConsole.mockResolvedValue({
      console_url: '',
      code_requires_worker: false,
    })
    const { result } = mount(workflow('start', 'code'), {
      whenNoWorkflow: false,
    })
    await waitFor(() => expect(mocks.fetchConsole).toHaveBeenCalled())
    expect(result.current).toEqual({ agent: false, code: false })
  })

  it('needs a Worker for agent workflows without asking the deployment', () => {
    const { result } = mount(workflow('start', 'agent'), {
      whenNoWorkflow: false,
    })
    expect(result.current).toEqual({ agent: true, code: false })
    expect(mocks.fetchConsole).not.toHaveBeenCalled()
  })

  it('falls back to whenNoWorkflow without a workflow', () => {
    expect(mount(null, { whenNoWorkflow: true }).result.current).toEqual({
      agent: true,
      code: false,
    })
    expect(mount(null, { whenNoWorkflow: false }).result.current).toEqual({
      agent: false,
      code: false,
    })
    expect(mocks.fetchConsole).not.toHaveBeenCalled()
  })

  it('does not fetch while disabled', () => {
    mount(workflow('code'), { enabled: false, whenNoWorkflow: false })
    expect(mocks.fetchConsole).not.toHaveBeenCalled()
  })
})
