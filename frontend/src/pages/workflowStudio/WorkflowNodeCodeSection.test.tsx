import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { WorkflowNodeCodeSection } from './WorkflowNodeCodeSection'
import { api } from '../../api'
import { useSettingStore } from '../../stores/settingStore'
import { useUiStore } from '../../stores/uiStore'
import type { WorkflowNodeRecord } from '../../types'
import type { ExecutorDefinition } from '../../types/executorTypes'

vi.mock('../../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

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
    { name: 'fetch_questions', path: 'workflow_nodes/question_intake.py' },
  ],
}

const BASE =
  '/api/workspaces/default/workflows/question_comprehension_info/nodes/fetch_questions/code'

const BUILTIN_CODE = 'def run(job, job_dir, runtime):\n    return None\n'
const CUSTOM_CODE = "def run(job, job_dir, runtime):\n    return 'custom'\n"

const builtinResponse = {
  origin: 'builtin',
  code: BUILTIN_CODE,
  path: 'workflow_nodes/question_intake.py',
  version: null,
  has_draft: false,
}

const customResponse = {
  origin: 'custom',
  code: CUSTOM_CODE,
  path: null,
  version: 1,
  has_draft: false,
}

function versionRow(version: number, status: string, note?: string) {
  return {
    id: `id-v${version}`,
    version,
    status,
    code: CUSTOM_CODE,
    code_hash: 'abc',
    created_by: 'user:u1',
    change_note: note ?? null,
    created_at: '2026-08-01T00:00:00Z',
    published_at: status === 'draft' ? null : '2026-08-01T01:00:00Z',
  }
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
    useSettingStore.setState({ workspaceId: 'default' })
    useSettingStore.getState().setSettings({
      workflowKey: 'question_comprehension_info',
    })
    useUiStore.setState({ toast: null })
    mockApi.mockResolvedValue(builtinResponse)
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

  it('loads builtin code read-only with a fork entry', async () => {
    renderSection()

    expect(
      await screen.findByText('def run(job, job_dir, runtime):', {
        exact: false,
      })
    ).toBeInTheDocument()
    expect(mockApi).toHaveBeenCalledWith(BASE)
    expect(screen.getByText(/内置/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'fork 为自定义节点' })
    ).toBeInTheDocument()
  })

  it('forks the builtin code into a draft via PUT', async () => {
    renderSection()
    await screen.findByText(/内置/)

    fireEvent.click(screen.getByRole('button', { name: 'fork 为自定义节点' }))
    const editor = screen.getByLabelText('节点代码内容')
    expect(editor).toHaveValue(BUILTIN_CODE)
    fireEvent.change(screen.getByLabelText('变更说明'), {
      target: { value: '初版' },
    })
    mockApi.mockResolvedValueOnce(versionRow(1, 'draft', '初版'))
    mockApi.mockResolvedValueOnce({ ...customResponse, has_draft: true })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('草稿已保存')
    )
    const [url, init] = mockApi.mock.calls[1]
    expect(url).toBe(BASE)
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toEqual({
      code: BUILTIN_CODE,
      change_note: '初版',
    })
  })

  it('publishes the draft of a custom node', async () => {
    mockApi.mockResolvedValue({ ...customResponse, has_draft: true })
    renderSection()
    await screen.findByText(/自定义 v1/)

    mockApi.mockResolvedValueOnce(versionRow(1, 'published'))
    mockApi.mockResolvedValueOnce(customResponse)
    fireEvent.click(screen.getByRole('button', { name: '发布' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        '已发布，新执行立即生效'
      )
    )
    expect(mockApi.mock.calls[1][0]).toBe(`${BASE}/publish`)
    expect(mockApi.mock.calls[1][1]?.method).toBe('POST')
  })

  it('lists versions and rolls back to an old one', async () => {
    mockApi.mockResolvedValue({ ...customResponse, version: 2 })
    renderSection()
    await screen.findByText(/自定义 v2/)

    mockApi.mockResolvedValueOnce({
      versions: [
        { ...versionRow(2, 'published'), code: undefined },
        { ...versionRow(1, 'archived', '初版'), code: undefined },
      ],
    })
    fireEvent.click(screen.getByRole('button', { name: '版本历史' }))

    expect(await screen.findByText(/v1 · 已归档/)).toBeInTheDocument()
    expect(screen.getByText(/user:u1 · 初版/)).toBeInTheDocument()

    mockApi.mockResolvedValueOnce(versionRow(3, 'published'))
    mockApi.mockResolvedValueOnce({ ...customResponse, version: 3 })
    mockApi.mockResolvedValueOnce({
      versions: [{ ...versionRow(3, 'published'), code: undefined }],
    })
    fireEvent.click(screen.getByRole('button', { name: '回滚到此版本' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toContain('已回滚到 v1')
    )
    const [url, init] = mockApi.mock.calls[2]
    expect(url).toBe(`${BASE}/rollback`)
    expect(JSON.parse(String(init?.body))).toEqual({ version: 1 })
  })

  it('resets to builtin after confirmation', async () => {
    mockApi.mockResolvedValue(customResponse)
    renderSection()
    await screen.findByText(/自定义 v1/)

    fireEvent.click(screen.getByRole('button', { name: '回落内置' }))
    mockApi.mockResolvedValueOnce({ archived: 2 })
    mockApi.mockResolvedValueOnce(builtinResponse)
    fireEvent.click(screen.getByRole('button', { name: '确认回落内置' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('已回落到内置实现')
    )
    expect(mockApi.mock.calls[1][1]?.method).toBe('DELETE')
  })

  it('shows an error when loading fails', async () => {
    mockApi.mockRejectedValue(new Error('HTTP 404'))
    renderSection()

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('HTTP 404')
    )
  })

  it('surfaces write permission errors inline', async () => {
    renderSection()
    await screen.findByText(/内置/)

    fireEvent.click(screen.getByRole('button', { name: 'fork 为自定义节点' }))
    mockApi.mockRejectedValueOnce(new Error('Insufficient workspace role'))
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Insufficient workspace role'
      )
    )
  })

  it('hides write controls in read-only revision mode', async () => {
    mockApi.mockResolvedValue(customResponse)
    renderSection({ readOnly: true })
    await screen.findByText(/自定义 v1/)

    expect(
      screen.queryByRole('button', { name: '编辑' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'fork 为自定义节点' })
    ).not.toBeInTheDocument()
    expect(screen.getByText(/历史版本查看模式/)).toBeInTheDocument()
  })
})
