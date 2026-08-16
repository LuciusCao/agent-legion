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
  key: 'fetch_items',
  label: '获取题目',
  capability: 'fetch_items',
  after: [],
  inputs: [],
  outputs: [],
}

const codeExecutor: ExecutorDefinition = {
  id: 'code-default',
  kind: 'code',
  global_capacity: 16,
  capabilities: ['fetch_items'],
  capability_details: [{ name: 'fetch_items' }],
}

const BASE =
  '/api/workspaces/default/workflows/demo_workflow/nodes/fetch_items/code'

const BUILTIN_CODE = 'def run(job, job_dir, runtime):\n    return None\n'
const CUSTOM_CODE = "def run(job, job_dir, runtime):\n    return 'custom'\n"

const builtinResponse = {
  origin: 'builtin',
  code: BUILTIN_CODE,
  version: null,
  has_draft: false,
  draft_code: null,
  draft_version: null,
}

const customResponse = {
  origin: 'custom',
  code: CUSTOM_CODE,
  version: 1,
  has_draft: false,
  draft_code: null,
  draft_version: null,
}

const DRAFT_CODE = "def run(job, job_dir, runtime):\n    return 'draft'\n"

const builtinWithDraft = {
  ...builtinResponse,
  has_draft: true,
  draft_code: DRAFT_CODE,
  draft_version: 1,
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
      workflowKey="demo_workflow"
      {...overrides}
    />
  )
}

describe('WorkflowNodeCodeSection', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useSettingStore.setState({ workspaceId: 'default' })
    useSettingStore.getState().setSettings({
      workflowKey: 'demo_workflow',
    })
    useUiStore.setState({ toast: null })
    mockApi.mockResolvedValue(builtinResponse)
  })

  it('renders nothing when the capability has no code path', () => {
    const piExecutor: ExecutorDefinition = {
      id: 'pi-default',
      kind: 'pi',
      global_capacity: 4,
      capabilities: ['fetch_items'],
      capability_details: [{ name: 'fetch_items' }],
    }
    const { container } = renderSection({ executorCatalog: [piExecutor] })
    expect(container.firstChild).toBeNull()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('uses the visible workflow key prop over the settings snapshot', async () => {
    // 草稿改 key 发布后 settings 快照与 visible workflow 分叉；代码区必须
    // 跟 binding editor 一样用 Inspector 下传的 key。
    useSettingStore.getState().setSettings({ workflowKey: 'stale_snapshot' })
    renderSection({ workflowKey: 'visible_wf' })

    await screen.findByText(/出厂版本/)
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/default/workflows/visible_wf/nodes/fetch_items/code'
    )
  })

  it('loads builtin code read-only with a fork entry', async () => {
    renderSection()

    expect(
      await screen.findByText('def run(job, job_dir, runtime):', {
        exact: false,
      })
    ).toBeInTheDocument()
    expect(mockApi).toHaveBeenCalledWith(BASE)
    expect(screen.getByText(/出厂版本/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'fork 为自定义节点' })
    ).toBeInTheDocument()
  })

  it('forks the builtin code into a draft via PUT', async () => {
    renderSection()
    await screen.findByText(/出厂版本/)

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
    await screen.findByText(/出厂版本/)

    fireEvent.click(screen.getByRole('button', { name: 'fork 为自定义节点' }))
    mockApi.mockRejectedValueOnce(new Error('Insufficient workspace role'))
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Insufficient workspace role'
      )
    )
  })

  it('shows the publish button for a builtin node with a draft', async () => {
    mockApi.mockResolvedValue(builtinWithDraft)
    renderSection()
    await screen.findByText(/有未发布草稿/)

    const publishButton = screen.getByRole('button', { name: '发布' })
    mockApi.mockResolvedValueOnce(versionRow(1, 'published'))
    mockApi.mockResolvedValueOnce(customResponse)
    fireEvent.click(publishButton)

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        '已发布，新执行立即生效'
      )
    )
    expect(mockApi.mock.calls[1][0]).toBe(`${BASE}/publish`)
  })

  it('loads the pending draft into the editor instead of the builtin code', async () => {
    mockApi.mockResolvedValue(builtinWithDraft)
    renderSection()
    await screen.findByText(/有未发布草稿/)

    fireEvent.click(screen.getByRole('button', { name: 'fork 为自定义节点' }))

    expect(screen.getByLabelText('节点代码内容')).toHaveValue(DRAFT_CODE)
  })

  it('expands a version to view its code', async () => {
    mockApi.mockResolvedValue({ ...customResponse, version: 2 })
    renderSection()
    await screen.findByText(/自定义 v2/)

    mockApi.mockResolvedValueOnce({
      versions: [{ ...versionRow(2, 'published'), code: undefined }],
    })
    fireEvent.click(screen.getByRole('button', { name: '版本历史' }))
    await screen.findByText(/v2 · 已发布/)

    mockApi.mockResolvedValueOnce(versionRow(2, 'published'))
    fireEvent.click(screen.getByRole('button', { name: '查看 v2 代码' }))

    expect(
      await screen.findByText("return 'custom'", { exact: false })
    ).toBeInTheDocument()
    expect(mockApi.mock.calls[2][0]).toBe(`${BASE}/versions/2`)
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

  it('offers 从模板新建 alongside fork for a builtin node', async () => {
    renderSection()
    await screen.findByText(/出厂版本/)

    expect(
      screen.getByRole('button', { name: 'fork 为自定义节点' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '从模板新建' })
    ).toBeInTheDocument()
  })

  it('creates a draft from the backend template for a pathless capability', async () => {
    const pathlessExecutor: ExecutorDefinition = {
      id: 'code-custom',
      kind: 'code',
      global_capacity: 1,
      capabilities: ['custom_only'],
      capability_details: [{ name: 'custom_only' }],
    }
    const pathlessNode: WorkflowNodeRecord = {
      ...node,
      key: 'do_custom',
      capability: 'custom_only',
    }
    const noneResponse = {
      origin: 'none',
      code: '',
      version: null,
      has_draft: false,
      draft_code: null,
      draft_version: null,
    }
    const templateCode = 'from workspace_libs.node_sdk import NodeContext\n'
    mockApi.mockResolvedValue(noneResponse)
    render(
      <WorkflowNodeCodeSection
        node={pathlessNode}
        executorCatalog={[pathlessExecutor]}
        workflowKey="demo_workflow"
      />
    )

    await screen.findByText(/无代码版本/)
    expect(
      screen.queryByRole('button', { name: 'fork 为自定义节点' })
    ).not.toBeInTheDocument()

    mockApi.mockResolvedValueOnce({ code: templateCode })
    mockApi.mockResolvedValueOnce(versionRow(1, 'draft'))
    fireEvent.click(screen.getByRole('button', { name: '从模板新建' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('已从模板创建草稿')
    )
    const customBase =
      '/api/workspaces/default/workflows/demo_workflow/nodes/do_custom/code'
    expect(mockApi.mock.calls[1][0]).toBe('/api/workflow-node-code-template')
    expect(mockApi.mock.calls[2][0]).toBe(customBase)
    expect(mockApi.mock.calls[2][1]?.method).toBe('PUT')
    expect(JSON.parse(String(mockApi.mock.calls[2][1]?.body))).toEqual({
      code: templateCode,
      change_note: null,
    })
  })

  it('lets a pathless node edit its existing draft', async () => {
    const pathlessExecutor: ExecutorDefinition = {
      id: 'code-custom',
      kind: 'code',
      global_capacity: 1,
      capabilities: ['custom_only'],
      capability_details: [{ name: 'custom_only' }],
    }
    const pathlessNode: WorkflowNodeRecord = {
      ...node,
      key: 'do_custom',
      capability: 'custom_only',
    }
    const templateCode = 'from workspace_libs.node_sdk import NodeContext\n'
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      version: null,
      has_draft: true,
      draft_code: templateCode,
      draft_version: 1,
    })
    render(
      <WorkflowNodeCodeSection
        node={pathlessNode}
        executorCatalog={[pathlessExecutor]}
        workflowKey="demo_workflow"
      />
    )

    await screen.findByText(/有未发布草稿/)
    expect(
      screen.queryByRole('button', { name: '从模板新建' })
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))

    expect(screen.getByLabelText('节点代码内容')).toHaveValue(templateCode)
  })
})
