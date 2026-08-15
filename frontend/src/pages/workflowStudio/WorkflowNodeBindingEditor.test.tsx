import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getExecutorCatalog } from '../../api/executorApi'
import { useSettingStore } from '../../stores/settingStore'
import { TestQueryProvider } from '../../testing/testQueryClient'
import type { WorkflowNodeRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'
import { WorkflowNodeBindingEditor } from './WorkflowNodeBindingEditor'
import { StudioNavContext, type StudioNav } from './workflowStudioNav'

vi.mock('../../api/executorApi', () => ({ getExecutorCatalog: vi.fn() }))

const mockGetCatalog = vi.mocked(getExecutorCatalog)

const node: WorkflowNodeRecord = {
  key: 'build',
  label: '构建',
  capability: 'fetch_questions',
  after: [],
  inputs: [],
  outputs: [],
  terminal: null,
}

const executorCatalog: ExecutorDefinition[] = [
  {
    id: 'code-default',
    kind: 'code',
    global_capacity: 16,
    capabilities: ['fetch_questions'],
    capability_details: [
      { name: 'fetch_questions', path: 'workflow_nodes/fetch_questions.py' },
    ],
  },
  {
    id: 'pi-runner',
    kind: 'pi',
    global_capacity: 4,
    capabilities: ['fetch_questions'],
    capability_details: [{ name: 'fetch_questions', skill: 'ns/skill' }],
  },
  {
    id: 'unallocated',
    kind: 'code',
    global_capacity: 2,
    capabilities: ['other_cap'],
    capability_details: [{ name: 'other_cap' }],
  },
]

const saveAllMock = vi.hoisted(() => vi.fn().mockResolvedValue(true))
const navMock = vi.hoisted(() => ({
  openAgent: vi.fn(),
  openExecutor: vi.fn(),
}))

function renderEditor(props?: {
  node?: WorkflowNodeRecord
  readOnly?: boolean
  workflowKey?: string
  nav?: StudioNav
}) {
  const target = props?.node ?? node
  return render(
    <TestQueryProvider>
      <StudioNavContext.Provider value={props?.nav ?? navMock}>
        <WorkflowNodeBindingEditor
          node={target}
          bindings={findCapabilityBindings(executorCatalog, target.capability)}
          executorCatalog={executorCatalog}
          workflowKey={props?.workflowKey ?? 'wf'}
          readOnly={props?.readOnly}
        />
      </StudioNavContext.Provider>
    </TestQueryProvider>
  )
}

function getSelectInput(nodeKey: string) {
  const root = screen.getByTestId(`studio-binding-select-${nodeKey}`)
  const input = root.querySelector('input')
  if (!input) throw new Error(`Select input not found for ${nodeKey}`)
  return input as HTMLInputElement
}

function changeSelectValue(nodeKey: string, value: string) {
  const input = getSelectInput(nodeKey)
  act(() => {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value'
    )?.set
    if (nativeInputValueSetter) {
      nativeInputValueSetter.call(input, value)
    } else {
      input.value = value
    }
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

describe('WorkflowNodeBindingEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetCatalog.mockResolvedValue({ executors: [], agents: [] })
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: {
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        workflowKey: 'wf',
      },
      executorConfiguration: {
        allocations: [
          {
            executor_id: 'code-default',
            workspace_id: 'ws1',
            concurrency_limit: 4,
          },
          {
            executor_id: 'pi-runner',
            workspace_id: 'ws1',
            concurrency_limit: 2,
          },
        ],
        bindings: [
          {
            workflow_key: 'wf',
            node_key: 'build',
            executor_id: 'code-default',
          },
        ],
        node_limits: [],
        migration_warnings: [],
        agent_capacity: null,
      },
      isSaving: false,
      saveAll: saveAllMock,
    })
  })

  it('shows the current binding and lists only allocated compatible executors', () => {
    renderEditor()

    expect(getSelectInput('build').value).toBe('code-default')
    fireEvent.mouseDown(screen.getByRole('combobox'))
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).toEqual(['未绑定', 'code-default (code)', 'pi-runner (pi)'])
  })

  it('updates the store binding and saves immediately on change', () => {
    renderEditor()

    changeSelectValue('build', 'pi-runner')

    expect(
      useSettingStore.getState().executorConfiguration.bindings
    ).toContainEqual({
      workflow_key: 'wf',
      node_key: 'build',
      executor_id: 'pi-runner',
    })
    expect(saveAllMock).toHaveBeenCalledTimes(1)
  })

  it('clears the binding when 未绑定 is selected', () => {
    renderEditor()

    changeSelectValue('build', '')

    expect(
      useSettingStore.getState().executorConfiguration.bindings
    ).toHaveLength(0)
    expect(saveAllMock).toHaveBeenCalledTimes(1)
  })

  it('rolls back the draft binding when the save fails', async () => {
    // 保存失败：服务端快照未变，Select 草稿必须回滚到旧绑定，避免与 DAG
    // 展示长期分叉（失败 toast 由 saveAll 负责）。
    saveAllMock.mockResolvedValueOnce(false)
    renderEditor()

    changeSelectValue('build', 'pi-runner')
    expect(
      useSettingStore.getState().executorConfiguration.bindings
    ).toContainEqual({
      workflow_key: 'wf',
      node_key: 'build',
      executor_id: 'pi-runner',
    })

    await act(async () => {})
    expect(
      useSettingStore.getState().executorConfiguration.bindings
    ).toContainEqual({
      workflow_key: 'wf',
      node_key: 'build',
      executor_id: 'code-default',
    })
  })

  it('warns and disables the executor jump when unbound', () => {
    useSettingStore.setState({
      executorConfiguration: {
        ...useSettingStore.getState().executorConfiguration,
        bindings: [],
      },
    })
    renderEditor()

    expect(
      screen.getByText('未绑定 executor，调度该节点将失败')
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开 Executor' })).toBeDisabled()
  })

  it('jumps to the bound executor editor', () => {
    renderEditor()

    act(() => {
      screen.getByRole('button', { name: '打开 Executor' }).click()
    })

    expect(navMock.openExecutor).toHaveBeenCalledWith('code-default')
  })

  it('offers a code section jump for code capabilities', () => {
    renderEditor()
    expect(
      screen.getByRole('button', { name: '查看节点代码' })
    ).toBeInTheDocument()
  })

  it('warns when no allocated executor supports the capability', () => {
    const orphan: WorkflowNodeRecord = {
      ...node,
      key: 'orphan',
      capability: 'other_cap',
    }
    renderEditor({ node: orphan })

    expect(
      screen.getByText('没有已分配的执行器支持能力 other_cap')
    ).toBeInTheDocument()
  })

  it('renders a read-only binding summary without the select', () => {
    renderEditor({ readOnly: true })

    expect(screen.getByText('绑定：code-default')).toBeInTheDocument()
    expect(
      screen.queryByTestId('studio-binding-select-build')
    ).not.toBeInTheDocument()
  })

  it('reads and writes bindings under the visible workflow key, not settings.workflowKey', () => {
    // settings 快照与 DAG 展示的 workflow 分叉（如草稿改 key 发布后）时，
    // 绑定编辑器必须与 DAG 口径（visible workflow key）一致。
    useSettingStore.setState({
      settings: {
        entityType: 'question',
        intakeModes: [],
        labelOverrides: {},
        workflowKey: 'legacy-wf',
      },
    })
    renderEditor({ workflowKey: 'wf' })

    expect(getSelectInput('build').value).toBe('code-default')
    changeSelectValue('build', 'pi-runner')

    expect(
      useSettingStore.getState().executorConfiguration.bindings
    ).toContainEqual({
      workflow_key: 'wf',
      node_key: 'build',
      executor_id: 'pi-runner',
    })
    expect(saveAllMock).toHaveBeenCalledTimes(1)
  })

  it('shows a retryable load error instead of the empty-binding hint when the catalog fails', async () => {
    mockGetCatalog.mockRejectedValue(new Error('boom'))
    const orphan: WorkflowNodeRecord = {
      ...node,
      key: 'orphan',
      capability: 'unknown_cap',
    }
    renderEditor({ node: orphan })

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'executor 目录加载失败'
    )
    expect(
      screen.queryByText('未匹配到 executor capability')
    ).not.toBeInTheDocument()

    mockGetCatalog.mockResolvedValue({ executors: [], agents: [] })
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    await waitFor(() =>
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    )
    expect(mockGetCatalog).toHaveBeenCalledTimes(2)
    expect(screen.getByText('未匹配到 executor capability')).toBeInTheDocument()
  })
})
