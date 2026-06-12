import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ExecutorAllocationRemovalDialog } from './ExecutorAllocationRemovalDialog'
import { useSettingStore } from '../stores/settingStore'

describe('ExecutorAllocationRemovalDialog', () => {
  beforeEach(() => {
    useSettingStore.setState({
      executorCatalog: [
        {
          id: 'local-default',
          kind: 'local' as const,
          capabilities: ['ingest'],
          global_capacity: 4,
        },
      ],
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            pipeline_key: 'question_content',
            node_key: 'ingest',
            executor_id: 'local-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
      },
      pendingAllocationRemoval: 'local-default',
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
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            pipeline_key: 'question_content',
            node_key: 'ingest',
            executor_id: 'local-default',
          },
        ],
        node_limits: [
          {
            pipeline_key: 'question_content',
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
