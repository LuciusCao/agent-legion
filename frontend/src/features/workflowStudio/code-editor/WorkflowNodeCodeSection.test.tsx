import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
  act,
} from '@testing-library/react'
import { WorkflowNodeCodeSection } from './WorkflowNodeCodeSection'
import { api } from '../../../api'
import { useSettingStore } from '../../../stores/settingStore'
import { useUiStore } from '../../../stores/uiStore'
import type { WorkflowNodeRecord } from '../../../types'

vi.mock('../../../api', () => ({
  fetchAgentRuntimes: vi.fn(() => Promise.resolve({ runtimes: {} })),
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

const node: WorkflowNodeRecord = {
  key: 'fetch_items',
  label: '获取题目',
  capability: 'fetch_items',
  // 显式 code 节点（#284）：类型判定只读 node_type。
  node_type: 'code',
  after: [],
  inputs: [],
  outputs: [],
}

const BASE = '/api/workspaces/default/nodes/fetch_items/code'

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
  // #749：GET 带出草稿身份（发布的 CAS 令牌），接口见后端 #749 契约。
  draft_code_hash: 'code-hash-draft',
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
  return render(<WorkflowNodeCodeSection node={node} {...overrides} />)
}

// codex #756 场景的起点形态：已有草稿的 custom 节点（version 2 的草稿，
// hash 'h-old'）。
const existingDraft = {
  ...customResponse,
  has_draft: true,
  draft_code: DRAFT_CODE,
  draft_version: 2,
  draft_code_hash: 'h-old',
}

// codex #756 场景脚手架：BASE GET 不自动落地，而是按调用顺序产出由测试
// 控制放行时机的 deferred（mount GET 与每次保存后的后台 reload GET 都在
// 队列里）；PUT 依次回 h1 / h2 两次保存响应——save_draft 原地更新已有
// 草稿，version 恒 2、hash 每次都变（后端 versioned_entities.save_draft
// 的 in-place update 语义），正是版本比较失灵的形态。
function mockPendingReloadSaves() {
  const gets: {
    promise: Promise<unknown>
    resolve: (value: unknown) => void
  }[] = []
  const puts = [
    { ...versionRow(2, 'draft'), code_hash: 'h1' },
    { ...versionRow(2, 'draft'), code_hash: 'h2' },
  ]
  mockApi.mockImplementation((path: unknown, init?: unknown) => {
    const method = (init as { method?: string } | undefined)?.method
    if (method === 'PUT') return Promise.resolve(puts.shift())
    if (path === `${BASE}/publish`) {
      return Promise.resolve(versionRow(2, 'published'))
    }
    if (String(path).startsWith(BASE)) {
      let resolve!: (value: unknown) => void
      const promise = new Promise<unknown>((r) => {
        resolve = r
      })
      gets.push({ promise, resolve })
      return promise
    }
    return Promise.resolve(customResponse)
  })
  return gets
}

describe('WorkflowNodeCodeSection', () => {
  beforeEach(() => {
    mockApi.mockReset()
    useSettingStore.setState({ workspaceId: 'default' })
    useSettingStore.getState().setSettings({})
    useUiStore.setState({ toast: null })
    mockApi.mockResolvedValue(builtinResponse)
  })

  it('renders nothing for an agent-typed node', () => {
    const { container } = renderSection({
      node: { ...node, node_type: 'agent' },
    })
    expect(container.firstChild).toBeNull()
    expect(mockApi).not.toHaveBeenCalled()
  })

  it('keys the code URL on the workspace id only', async () => {
    // workflows/{workflowKey} 路径段已退役（#211）：key 与 workspace id
    // 恒等，节点代码路由改挂 workspace 下。
    useSettingStore.getState().setSettings({ workflowKey: 'stale_snapshot' })
    renderSection()

    await screen.findByText(/出厂版本/)
    expect(mockApi).toHaveBeenCalledWith(
      '/api/workspaces/default/nodes/fetch_items/code'
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

  it('opens the wide-view dialog with line numbers and closes it', async () => {
    renderSection()
    await screen.findByText(/出厂版本/)

    fireEvent.click(screen.getByRole('button', { name: '宽视图' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('节点代码 · fetch_items')
    expect(dialog).toHaveTextContent('def run(job, job_dir, runtime):')
    // 行号渲染（两行代码 → 至少出现行号 1 和 2）。
    expect(within(dialog).getByText('1')).toBeInTheDocument()
    expect(within(dialog).getByText('2')).toBeInTheDocument()

    fireEvent.click(
      within(dialog).getByRole('button', { name: '关闭代码宽视图' })
    )
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    )
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
    mockApi.mockResolvedValue({
      ...customResponse,
      has_draft: true,
      draft_code_hash: 'h1',
    })
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
    const [url, init] = mockApi.mock.calls[1]
    expect(url).toBe(`${BASE}/publish`)
    expect(init?.method).toBe('POST')
    // #749：发布携带详情读取的草稿 hash（expected_hash CAS）。
    expect(JSON.parse(String(init?.body))).toEqual({ expected_hash: 'h1' })
  })

  // #749：保存→发布之间草稿被其他会话/编辑器覆盖，正是 expected_hash 要
  // 抓的竞态。409 专用文案引导重新加载（与聊天草稿卡同一交互模式）。
  it('shows the CAS conflict hint when publish returns 409', async () => {
    mockApi.mockResolvedValue({
      ...customResponse,
      has_draft: true,
      draft_code_hash: 'h1',
    })
    renderSection()
    await screen.findByText(/自定义 v1/)

    mockApi.mockRejectedValueOnce(
      Object.assign(
        new Error('draft hash mismatch for node_code wf:fetch_items'),
        {
          status: 409,
        }
      )
    )
    fireEvent.click(screen.getByRole('button', { name: '发布' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        '草稿已被其他会话或编辑器更新，请重新加载后再保存发布'
      )
    )
    expect(useUiStore.getState().toast).toBeNull()
    expect(screen.getByRole('button', { name: '发布' })).toBeEnabled()
  })

  // #749 修（review P3-3）：404 = 无草稿可发（刚在别处发布过），对齐
  // EntityDraftPublishButton 的可行动文案。
  it('shows the no-draft hint when publish returns 404', async () => {
    mockApi.mockResolvedValue({
      ...customResponse,
      has_draft: true,
      draft_code_hash: 'h1',
    })
    renderSection()
    await screen.findByText(/自定义 v1/)

    mockApi.mockRejectedValueOnce(
      Object.assign(new Error('no draft for node_code wf:fetch_items'), {
        status: 404,
      })
    )
    fireEvent.click(screen.getByRole('button', { name: '发布' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        '没有待发布的草稿（可能刚已发布过）'
      )
    )
  })

  // #749 修（review P2-2）：保存→立即发布必须携带保存响应回填的新 hash
  // ——PUT 响应的 code_hash 同步进 state，不等 fire-and-forget 的 reload
  // （对齐 AgentEditor.handleSaveDraft；修前闭包里还是旧 hash，撞假 409）。
  it('publishes immediately after saving, carrying the saved draft hash', async () => {
    mockApi.mockResolvedValue(customResponse)
    renderSection()
    await screen.findByText(/自定义 v1/)

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('节点代码内容'), {
      target: { value: DRAFT_CODE },
    })
    // 保存流程会触发两次 BASE GET（保存后的后台 reload、发布后的 reload），
    // 与用户的「立即发布」赛跑。R2 P2 修：第 1 次 reload 回 pre-save 形态
    // ——旧草稿身份（hash 'stale-from-reload'、draft_version 1），使断言
    // 的 'abc' 只能来自 PUT 响应的同步回填：删掉回填代码本测试必红（突变
    // 自检已在本地验证）。旧 draft_version（1 < 回填的 2）同时驱动 reload
    // 的函数式合并保留较新身份（R2 P3）。第 2 次起是发布后的 reload
    // （无草稿）。PUT 与 publish 各自独立响应。
    let baseGetCount = 0
    mockApi.mockImplementation(async (path: unknown, init?: unknown) => {
      if (init && (init as { method?: string }).method === 'PUT') {
        return versionRow(2, 'draft')
      }
      if (path === `${BASE}/publish`) {
        return versionRow(2, 'published')
      }
      if (String(path).startsWith(BASE)) {
        baseGetCount += 1
        if (baseGetCount === 1) {
          return {
            ...customResponse,
            has_draft: true,
            draft_code: DRAFT_CODE,
            draft_version: 1,
            draft_code_hash: 'stale-from-reload',
          }
        }
        return { ...customResponse, version: 2 }
      }
      return customResponse
    })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('草稿已保存')
    )
    fireEvent.click(screen.getByRole('button', { name: '发布' }))

    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        '已发布，新执行立即生效'
      )
    )
    const publishCall = mockApi.mock.calls.find(
      ([path]) => path === `${BASE}/publish`
    )
    expect(publishCall).toBeDefined()
    expect(publishCall![1]?.method).toBe('POST')
    // versionRow 的 code_hash 是 'abc'：保存响应同步回填，不等 reload
    //（reload GET 全程不出现 'abc'——首个 reload 回的是旧身份
    // 'stale-from-reload'，此断言只能由回填路径满足）。
    expect(JSON.parse(String(publishCall![1]?.body))).toEqual({
      expected_hash: 'abc',
    })
  })

  // #749 修（codex #756 P2）：用户连续保存同一份已有草稿——save_draft
  // 原地更新草稿行、draft_version 不递增。保存 #1 触发的后台 reload 在
  // 保存 #2 完成后才返回：其版本与本地新草稿相等，版本比较（`<`）判
  // false，旧 draft_code_hash（h1）会覆盖保存 #2 回填的新 hash（h2）→
  // 发布带旧 expected_hash 稳定 409。修：reload 响应按请求代次丢弃，
  // 非最新代次的响应整体作废，与版本号无关。
  it("keeps the second save hash when the first save's stale reload returns late", async () => {
    const gets = mockPendingReloadSaves()
    renderSection()
    gets[0].resolve(existingDraft) // mount GET（gen 1）落地
    await screen.findByText(/有未发布草稿/)

    // 保存 #1（gen 2 的 reload 挂起不放行）→ 保存响应回填 h1。
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('草稿已保存')
    )
    // 保存 #2（gen 3 的 reload 挂起）→ 回填 h2。
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('草稿已保存')
    )

    // 保存 #1 触发的旧 reload（gen 2）此刻才返回：version 与本地相等、
    // hash 是保存 #1 的 h1——codex 场景的毒响应。修后必须被代次门整体
    // 丢弃，不碰保存 #2 回填的 h2。
    gets[1].resolve({ ...existingDraft, draft_code_hash: 'h1' })
    await act(async () => {}) // 冲刷微任务：旧响应的 .then 先于发布执行

    fireEvent.click(screen.getByRole('button', { name: '发布' }))
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe(
        '已发布，新执行立即生效'
      )
    )
    const publishCall = mockApi.mock.calls.find(
      ([path]) => path === `${BASE}/publish`
    )
    expect(publishCall).toBeDefined()
    // h2 只能来自保存 #2 的同步回填：若旧 reload 覆盖了它，这里会是 h1
    //（突变自检：去掉代次校验本断言必红，发布携带 h1）。
    expect(JSON.parse(String(publishCall![1]?.body))).toEqual({
      expected_hash: 'h2',
    })
  })

  // #749 修（codex #756 P2）配套：代次门只丢「被更新的 reload 覆盖的旧
  // 请求」，最新发出的 reload 必须正常落地——「他端更新」场景里本地最后
  // 发出的 reload 就是最新代次，不能被误杀。这里旧 reload（gen 2）滞后
  // 返回被丢弃，最新 reload（gen 3）随后带回他端已发布的快照并正常采纳。
  it('lets the latest reload land when an older one resolves late', async () => {
    const gets = mockPendingReloadSaves()
    renderSection()
    gets[0].resolve(existingDraft)
    await screen.findByText(/有未发布草稿/)

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('草稿已保存')
    )
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))
    await waitFor(() =>
      expect(useUiStore.getState().toast?.message).toBe('草稿已保存')
    )

    // 旧 reload（gen 2，保存 #1 触发）带 h1 滞后返回：被代次门丢弃。
    gets[1].resolve({ ...existingDraft, draft_code_hash: 'h1' })
    await act(async () => {})
    // 最新 reload（gen 3，保存 #2 触发）返回：他端已把草稿发布——
    // has_draft false、版本 3。正常落地，不被误丢。
    gets[2].resolve({ ...customResponse, version: 3 })

    expect(await screen.findByText(/自定义 v3/)).toBeInTheDocument()
    expect(screen.queryByText(/有未发布草稿/)).not.toBeInTheDocument()
  })

  // #749 修（review P3-2）：GET 有草稿但没带回 draft_code_hash（版本偏斜，
  // 旧后端）→ 发布按钮禁用 + title 说明，而不是可点后 reject（对齐
  // EntityDraftPublishButton 的 null-hash 立场：无令牌发布退回无核对语义）。
  it('disables publish with a title hint when a draft has no hash (old backend)', async () => {
    mockApi.mockResolvedValue({
      ...customResponse,
      has_draft: true,
      draft_code: DRAFT_CODE,
      draft_code_hash: null,
    })
    renderSection()
    await screen.findByText(/有未发布草稿/)

    const publishButton = screen.getByRole('button', { name: '发布' })
    expect(publishButton).toBeDisabled()
    // 禁用按钮不触发自身 hover——title 挂在外层 span 上（与聊天草稿卡
    // 同一可达性形态）。
    expect(publishButton.closest('span')).toHaveAttribute(
      'title',
      '草稿缺少可核对的版本标识（后端版本偏斜），请升级后端再发布'
    )
    fireEvent.click(publishButton)
    expect(mockApi.mock.calls).toHaveLength(1)
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
    // #749：builtin+草稿形态同样携带草稿 hash（MCP 工具面保存的草稿由
    // GET 带出身份，人从检查器面板发布）。
    expect(JSON.parse(String(mockApi.mock.calls[1][1]?.body))).toEqual({
      expected_hash: 'code-hash-draft',
    })
  })

  it('loads the pending draft into the editor instead of the builtin code', async () => {
    mockApi.mockResolvedValue(builtinWithDraft)
    renderSection()
    await screen.findByText(/有未发布草稿/)

    fireEvent.click(screen.getByRole('button', { name: 'fork 为自定义节点' }))

    expect(screen.getByLabelText('节点代码内容')).toHaveValue(DRAFT_CODE)
  })

  it('shows the configured size ceiling in the editor (issue #628)', async () => {
    // 编辑器展示后端下发的实例级体积上限（默认 64KB）。
    mockApi.mockResolvedValue({ ...builtinResponse, max_code_bytes: 131072 })
    renderSection()

    await screen.findByText(/出厂版本/)
    fireEvent.click(screen.getByRole('button', { name: 'fork 为自定义节点' }))

    expect(
      screen.getByText(/代码体积上限 128 KB（实例配置）/)
    ).toBeInTheDocument()
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
    render(<WorkflowNodeCodeSection node={pathlessNode} />)

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
    const customBase = '/api/workspaces/default/nodes/do_custom/code'
    expect(mockApi.mock.calls[1][0]).toBe('/api/workflow-node-code-template')
    expect(mockApi.mock.calls[2][0]).toBe(customBase)
    expect(mockApi.mock.calls[2][1]?.method).toBe('PUT')
    expect(JSON.parse(String(mockApi.mock.calls[2][1]?.body))).toEqual({
      code: templateCode,
      change_note: null,
    })
  })

  it('lets a pathless node edit its existing draft', async () => {
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
    render(<WorkflowNodeCodeSection node={pathlessNode} />)

    await screen.findByText(/有未发布草稿/)
    expect(
      screen.queryByRole('button', { name: '从模板新建' })
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))

    expect(screen.getByLabelText('节点代码内容')).toHaveValue(templateCode)
  })
})
