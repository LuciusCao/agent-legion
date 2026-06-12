import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ExecutorAllocationSection } from './ExecutorAllocationSection'
import { useSettingStore } from '../stores/settingStore'

const catalog = [
  {
    id: 'local-default',
    kind: 'local' as const,
    capabilities: ['ingest'],
    global_capacity: 4,
  },
  {
    id: 'pi-default',
    kind: 'pi' as const,
    capabilities: ['review'],
    global_capacity: 2,
  },
  {
    id: 'openclaw-main',
    kind: 'openclaw' as const,
    capabilities: ['generate'],
    global_capacity: 3,
  },
]

describe('ExecutorAllocationSection', () => {
  beforeEach(() => {
    useSettingStore.setState({
      workspaceId: 'ws1',
      executorCatalog: catalog,
      executorConfiguration: {
        allocations: [],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
      pendingAllocationRemoval: null,
    })
  })

  it('lists every YAML executor with kind, capabilities, and global capacity', () => {
    render(<ExecutorAllocationSection />)

    expect(screen.getByText('local-default')).toBeInTheDocument()
    expect(screen.getByText('pi-default')).toBeInTheDocument()
    expect(screen.getByText('openclaw-main')).toBeInTheDocument()
    expect(screen.getAllByText('local').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('pi').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('openclaw').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('全局容量: 4')).toBeInTheDocument()
    expect(screen.getByText('全局容量: 2')).toBeInTheDocument()
    expect(screen.getByText('全局容量: 3')).toBeInTheDocument()
    expect(screen.getByText('支持能力: ingest')).toBeInTheDocument()
    expect(screen.getByText('支持能力: review')).toBeInTheDocument()
    expect(screen.getByText('支持能力: generate')).toBeInTheDocument()
  })

  it('allocation toggle is off when the workspace has no allocation', () => {
    render(<ExecutorAllocationSection />)

    const switches = document.querySelectorAll('md-switch')
    expect(switches.length).toBe(catalog.length)
    switches.forEach((switchEl) => {
      expect(switchEl).not.toHaveAttribute('selected')
    })
  })

  it('workspace limit input appears only for allocated executors', () => {
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
    })

    render(<ExecutorAllocationSection />)

    const inputs = document.querySelectorAll(
      'md-outlined-text-field[label="工作空间上限"]'
    )
    expect(inputs.length).toBe(1)
    expect(inputs[0]).toHaveAttribute('value', '2')
  })

  it('input min is 1 and max is the executor global capacity', () => {
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
    })

    render(<ExecutorAllocationSection />)

    const input = document.querySelector(
      'md-outlined-text-field[label="工作空间上限"]'
    )
    expect(input).toHaveAttribute('min', '1')
    expect(input).toHaveAttribute('max', '4')
  })

  it('removing an unused allocation happens immediately', async () => {
    useSettingStore.setState({
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'local-default',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [],
        node_limits: [],
        migration_warnings: [],
      },
    })

    render(<ExecutorAllocationSection />)

    const switchEl = document.querySelector('md-switch') as HTMLElement
    fireEvent.click(switchEl)

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toEqual([])
      expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
    })
  })

  it('removing an allocation used by bindings opens confirmation and lists affected nodes', async () => {
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
        node_limits: [],
        migration_warnings: [],
      },
    })

    render(<ExecutorAllocationSection />)

    const switchEl = document.querySelector('md-switch') as HTMLElement
    fireEvent.click(switchEl)

    await waitFor(() => {
      expect(
        screen.getByText('移除执行器会同时清除以下节点绑定')
      ).toBeInTheDocument()
      expect(screen.getByText('question_content / ingest')).toBeInTheDocument()
    })
  })

  it('cancel preserves all state', async () => {
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
        node_limits: [],
        migration_warnings: [],
      },
    })

    render(<ExecutorAllocationSection />)

    const switchEl = document.querySelector('md-switch') as HTMLElement
    fireEvent.click(switchEl)

    await waitFor(() => {
      expect(screen.getByText('取消')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('取消'))

    await waitFor(() => {
      expect(
        useSettingStore.getState().executorConfiguration.allocations
      ).toHaveLength(1)
      expect(
        useSettingStore.getState().executorConfiguration.bindings
      ).toHaveLength(1)
      expect(useSettingStore.getState().pendingAllocationRemoval).toBeNull()
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

    render(<ExecutorAllocationSection />)

    const switchEl = document.querySelector('md-switch') as HTMLElement
    fireEvent.click(switchEl)

    await waitFor(() => {
      expect(screen.getByText('确认')).toBeInTheDocument()
    })

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
})
