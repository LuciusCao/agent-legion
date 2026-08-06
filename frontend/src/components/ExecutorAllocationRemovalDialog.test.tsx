import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ExecutorAllocationRemovalDialog } from './ExecutorAllocationRemovalDialog'
import { useSettingStore } from '../stores/settingStore'

// executorCatalog 已迁入 react-query；mock 快照 hook，draft 仍写 store。
vi.mock('../hooks/useWorkspaceSettingsQuery', () => ({
  useWorkspaceSettingsSnapshot: () => ({
    workflowDefinition: null,
    executorCatalog: [
      {
        id: 'code-default',
        kind: 'code' as const,
        capabilities: ['ingest'],
        global_capacity: 4,
      },
    ],
    agentRoutes: [],
  }),
}))

describe('ExecutorAllocationRemovalDialog', () => {
  beforeEach(() => {
    useSettingStore.setState({
      workspaceId: 'ws1',
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            executor_id: 'code-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
      pendingAllocationRemoval: 'code-default',
    })
  })

  it('lists affected nodes', () => {
    render(<ExecutorAllocationRemovalDialog />)

    expect(
      screen.getByText('移除执行器会同时清除以下节点绑定')
    ).toBeInTheDocument()
    expect(screen.getByText('question_content / ingest')).toBeInTheDocument()
  })

  it('cancel preserves all state', async () => {
    render(<ExecutorAllocationRemovalDialog />)

    fireEvent.click(screen.getByText('取消'))

    await waitFor(() => {
      expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toHaveLength(1)
      expect(
        useSettingStore.getState().executorConfiguration.bindings
      ).toHaveLength(1)
    })
  })

  it('confirm removes allocation, bindings, and limits', async () => {
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            executor_id: 'code-default',
          },
        ],
        node_limits: [
          {
            workflow_key: 'question_content',
            node_key: 'ingest',
            concurrency_limit: 2,
          },
        ],
        migration_warnings: [],
      },
    })

    render(<ExecutorAllocationRemovalDialog />)

    fireEvent.click(screen.getByText('确认'))

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toEqual([])
      expect(useSettingStore.getState().executorConfiguration.bindings).toEqual(
        []
      )
      expect(
        useSettingStore.getState().executorConfiguration.node_limits
      ).toEqual([])
      expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
    })
  })

  it('renders nothing when no pending removal', () => {
    useSettingStore.setState({ pendingAllocationRemoval: null })
    const { container } = render(<ExecutorAllocationRemovalDialog />)
    expect(container.firstChild).toBeNull()
  })
})
