import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { WorkflowNodeCodeSection } from './WorkflowNodeCodeSection'
import { api } from '../../api'
import type { UserResponse } from '../../api/authApi'
import { useAuthStore } from '../../stores/authStore'
import { useUiStore } from '../../stores/uiStore'
import type { WorkflowNodeRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'

vi.mock('../../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

const adminUser: UserResponse = {
  id: 'u1',
  username: 'admin',
  display_name: 'Admin',
  role: 'admin',
  disabled_at: null,
  created_at: '2026-01-01T00:00:00Z',
}

const node: WorkflowNodeRecord = {
  key: 'fetch_questions',
  label: '获取题目',
  capability: 'fetch_questions',
  after: [],
  inputs: [],
  outputs: [],
}

const codeExecutor: ExecutorDefinition = {
  id: 'code-default',
  kind: 'code',
  global_capacity: 16,
  capabilities: ['fetch_questions'],
  capability_details: [
    {
      name: 'fetch_questions',
      path: 'workflow_nodes/fetch_questions.py',
    },
  ],
}

const fileResponse = {
  path: 'workflow_nodes/fetch_questions.py',
  content: 'def run(inputs):\n    return {}\n',
  capabilities: [
    { executor_id: 'code-default', capability: 'fetch_questions' },
  ],
}

function renderSection(
  overrides?: Partial<Parameters<typeof WorkflowNodeCodeSection>[0]>
) {
  return render(
    <WorkflowNodeCodeSection
      node={node}
      executorCatalog={[codeExecutor]}
      {...overrides}
    />
  )
}

describe('WorkflowNodeCodeSection', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useAuthStore.setState({ user: adminUser, status: 'authenticated' })
    useUiStore.setState({ toast: null })
    mockApi.mockResolvedValue(fileResponse)
  })

  it('renders nothing for non-admin users', () => {
    useAuthStore.setState({
      user: { ...adminUser, role: 'member' },
      status: 'authenticated',
    })
    const { container } = renderSection()
    expect(container.firstChild).toBeNull()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('renders nothing when the capability has no code path', () => {
    const piExecutor: ExecutorDefinition = {
      id: 'pi-default',
      kind: 'pi',
      global_capacity: 4,
      capabilities: ['fetch_questions'],
      capability_details: [{ name: 'fetch_questions' }],
    }
    const { container } = renderSection({ executorCatalog: [piExecutor] })
    expect(container.firstChild).toBeNull()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('loads and shows the code file with path and capability references', async () => {
    renderSection()

    expect(
      await screen.findByText('def run(inputs):', { exact: false })
    ).toBeInTheDocument()
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workflow-nodes/files/workflow_nodes/fetch_questions.py'
    )
    expect(
      screen.getByText('workflow_nodes/fetch_questions.py')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/code-default: fetch_questions/)
    ).toBeInTheDocument()
    expect(screen.getByText(/代码保存后立即生效/)).toBeInTheDocument()
  })

  it('shows an error when the load fails', async () => {
    mockApi.mockRejectedValue(new Error('HTTP 404'))
    renderSection()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('HTTP 404')
    )
  })

  it('saves edits and returns to the read-only view', async () => {
    renderSection()
    await screen.findByText('def run(inputs):', { exact: false })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    const editor = screen.getByLabelText('节点代码内容')
    fireEvent.change(editor, { target: { value: 'def run():\n    pass\n' } })
    mockApi.mockResolvedValue({
      path: 'workflow_nodes/fetch_questions.py',
      capabilities: fileResponse.capabilities,
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        '节点代码已保存，下次执行生效'
      )
    )
    const [path, init] = mockApi.mock.calls[1]
    expect(path).toBe(
      '/api/workflow-nodes/files/workflow_nodes/fetch_questions.py'
    )
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({
      content: 'def run():\n    pass\n',
    })
    expect(screen.queryByLabelText('节点代码内容')).not.toBeInTheDocument()
    expect(screen.getByText('def run():', { exact: false })).toBeInTheDocument()
  })

  it('shows the 422 error and stays in edit mode when the save fails', async () => {
    renderSection()
    await screen.findByText('def run(inputs):', { exact: false })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('节点代码内容'), {
      target: { value: 'not python' },
    })
    mockApi.mockRejectedValue(new Error('缺少模块级 run 函数'))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('缺少模块级 run 函数')
    )
    expect(screen.getByLabelText('节点代码内容')).toBeInTheDocument()
  })

  it('discards edits on cancel', async () => {
    renderSection()
    await screen.findByText('def run(inputs):', { exact: false })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('节点代码内容'), {
      target: { value: 'changed' },
    })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(screen.queryByLabelText('节点代码内容')).not.toBeInTheDocument()
    expect(
      screen.getByText('def run(inputs):', { exact: false })
    ).toBeInTheDocument()
    expect(mockApi).toHaveBeenCalledTimes(1)
  })

  it('keeps the immediate-effect hint in read-only revision mode', async () => {
    renderSection({ readOnly: true })
    await screen.findByText('def run(inputs):', { exact: false })

    expect(screen.getByText(/历史版本查看模式/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
  })
})
