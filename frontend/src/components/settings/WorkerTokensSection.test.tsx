import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type { ReactElement } from 'react'
import { WorkerTokensSection } from './WorkerTokensSection'
import {
  createRegisterToken,
  deleteAgentWorker,
  deleteRegisterToken,
  fetchWorkspaces,
  listAgentWorkers,
  listRegisterTokens,
} from '../../api'
import { TestQueryProvider } from '../../testing/testQueryClient'

vi.mock('../../api', () => ({
  createRegisterToken: vi.fn(),
  deleteAgentWorker: vi.fn(),
  deleteRegisterToken: vi.fn(),
  listAgentWorkers: vi.fn(),
  listRegisterTokens: vi.fn(),
  fetchWorkspaces: vi.fn(),
}))

vi.mock('../../hooks/useWorkerConsoleUrl', () => ({
  useWorkerConsoleUrl: () => 'http://127.0.0.1:8789',
}))

const mockListRegisterTokens = vi.mocked(listRegisterTokens)
const mockListAgentWorkers = vi.mocked(listAgentWorkers)
const mockCreateRegisterToken = vi.mocked(createRegisterToken)
const mockDeleteAgentWorker = vi.mocked(deleteAgentWorker)
const mockDeleteRegisterToken = vi.mocked(deleteRegisterToken)
const mockFetchWorkspaces = vi.mocked(fetchWorkspaces)

const WORKSPACE_ID = 'demo_video_workflow'

const sampleToken = {
  token_id: 't1',
  label: 'home-mac-mini',
  workspace_id: WORKSPACE_ID,
  created_at: '2026-07-01T00:00:00Z',
  revoked: false,
}

const sampleWorker = {
  worker_id: 'w1',
  name: 'mac-mini',
  online: true,
  last_seen_at: '2026-07-26T00:00:00Z',
  revoked: false,
  allowed_workspaces: [WORKSPACE_ID],
  register_token_ids: [],
  capabilities: [],
  labels: {},
  max_concurrency: 2,
  max_code_concurrency: 0,
  models: [],
  protocol_version: 1,
  registered_at: '2026-07-01T00:00:00Z',
  runtimes: ['pi'],
}

beforeEach(() => {
  vi.clearAllMocks()
  mockListRegisterTokens.mockResolvedValue([sampleToken])
  mockListAgentWorkers.mockResolvedValue([sampleWorker])
  mockFetchWorkspaces.mockResolvedValue({
    workspaces: [
      {
        id: WORKSPACE_ID,
        name: '演示工作区',
        description: '',
        created_at: '2026-07-01T00:00:00Z',
        updated_at: '2026-07-01T00:00:00Z',
        default_entity: '',
        node_config: {},
        node_config_json: '{}',
        resource_config: {},
        resource_config_json: '{}',
      },
    ],
  })
})

function renderWithClient(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

function renderSection() {
  return renderWithClient(<WorkerTokensSection workspaceId={WORKSPACE_ID} />)
}

describe('WorkerTokensSection', () => {
  it('loads key and worker lists on mount without any credential', async () => {
    renderSection()

    await waitFor(() => {
      expect(screen.getByText('home-mac-mini')).toBeTruthy()
    })
    expect(mockListRegisterTokens).toHaveBeenCalledWith()
    // 数据层仍全量拉取（#1141：deletable 判定需要完整 worker↔key 画面），
    // 展示层才按 workspace 过滤。
    expect(mockListAgentWorkers).toHaveBeenCalledWith(
      undefined,
      expect.any(AbortSignal)
    )
    expect(screen.getByText('mac-mini')).toBeTruthy()
    // key 行展示短 Key ID，便于与 Worker 侧 token 前缀对应。
    expect(screen.getByTestId('register-token-t1').textContent).toContain('t1')
  })

  it('only lists keys bound to the current workspace', async () => {
    mockListRegisterTokens.mockResolvedValue([
      sampleToken,
      {
        ...sampleToken,
        token_id: 't9',
        label: 'other-ws-key',
        workspace_id: 'other_ws',
      },
    ])
    renderSection()

    await waitFor(() => {
      expect(screen.getByText('home-mac-mini')).toBeTruthy()
    })
    expect(screen.queryByText('other-ws-key')).toBeNull()
  })

  it('only lists workers serving the current workspace (#1141)', async () => {
    mockListAgentWorkers.mockResolvedValue([
      sampleWorker,
      {
        ...sampleWorker,
        worker_id: 'w9',
        name: 'other-ws-mac',
        allowed_workspaces: ['other_ws'],
      },
      {
        // legacy 全局注册（scope=[]，allow-all）实际承接本 ws 任务，
        // 显示（评审 P2：与 EXEC-WORKERACL-001 对齐）。
        ...sampleWorker,
        worker_id: 'w8',
        name: 'legacy-mac',
        allowed_workspaces: [],
      },
    ])
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('worker-w1')).toBeTruthy()
    })
    expect(screen.queryByTestId('worker-w9')).toBeNull()
    expect(screen.queryByText('other-ws-mac')).toBeNull()
    // [] 是 allow-all：legacy worker 显示，且带「待迁移」chip。
    expect(screen.getByTestId('worker-w8')).toBeTruthy()
    expect(screen.getByTestId('worker-w8').textContent).toContain(
      '待迁移（旧全局注册）'
    )
  })

  it('renders a multi-workspace worker with the current one first (#1141)', async () => {
    mockListAgentWorkers.mockResolvedValue([
      {
        ...sampleWorker,
        worker_id: 'w2',
        name: 'shared-mac',
        allowed_workspaces: ['demo_workspace', WORKSPACE_ID],
      },
    ])
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('worker-w2')).toBeTruthy()
    })
    const item = screen.getByTestId('worker-w2')
    // 服务多 workspace 的 worker 仍显示（承接本 workspace 任务）；其它
    // workspace 不再逐一列名，只留本 workspace 名 + 计数。
    expect(item.textContent).toContain('演示工作区')
    expect(item.textContent).toContain('+1 个其它 workspace')
    expect(item.textContent).not.toContain('demo_workspace')
  })

  it('shows online status and workspace scope chips for workers', async () => {
    mockListAgentWorkers.mockResolvedValue([
      sampleWorker,
      {
        ...sampleWorker,
        worker_id: 'w2',
        name: 'scoped-mac',
        online: false,
        allowed_workspaces: ['demo_video_workflow', 'demo_workspace'],
      },
    ])
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('worker-w1')).toBeTruthy()
    })
    const firstItem = screen.getByTestId('worker-w1')
    expect(firstItem.textContent).toContain('在线')

    const scopedItem = screen.getByTestId('worker-w2')
    expect(scopedItem.textContent).toContain('离线')
    // workspace 名称优先显示；其它 workspace 折叠为计数。
    expect(scopedItem.textContent).toContain('演示工作区')
    expect(scopedItem.textContent).toContain('+1 个其它 workspace')
  })

  it('shows legacy allow-all workers with the migration chip (#1141 P2)', async () => {
    // [] 是 allow-all（EXEC-WORKERACL-001，与 claim 准入 / code_dispatch /
    // monitoring 面板 agentWorkerRows 同一谓词）：legacy 全局注册的 worker
    // 实际承接本 ws 任务，该 workspace 视图必须显示，空态文案不得误报。
    mockListAgentWorkers.mockResolvedValue([
      {
        ...sampleWorker,
        allowed_workspaces: [],
      },
    ])
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('worker-w1')).toBeTruthy()
    })
    expect(screen.queryByText(/暂无已注册 Worker/)).toBeNull()
    const item = screen.getByTestId('worker-w1')
    // 「待迁移」chip 复活为可达路径：替代 scope chip（无 workspace 名、
    // 无 +N 计数——[] 形态不走折叠逻辑）。
    expect(item.textContent).toContain('待迁移（旧全局注册）')
    expect(item.textContent).not.toContain('演示工作区')
    expect(item.textContent).not.toContain('个其它 workspace')
    // 无绑定记录的 legacy worker 保留删除入口（admin 的唯一清理路径）。
    expect(item.querySelectorAll('button')).toHaveLength(1)
    expect(item.querySelector('button')?.textContent).toBe('删除')
  })

  it('shows an error when loading fails', async () => {
    mockListRegisterTokens.mockRejectedValue(new Error('HTTP 500'))
    renderSection()

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('HTTP 500')
    })
  })

  it('guides the admin to the Worker console after issuing a key', async () => {
    mockCreateRegisterToken.mockResolvedValue({
      token_id: 't2',
      register_token: 'plain-secret',
      workspace_id: WORKSPACE_ID,
      label: 'new-worker',
    })
    renderSection()
    await waitFor(() => screen.getByText('home-mac-mini'))

    fireEvent.change(screen.getByLabelText('Key 名称'), {
      target: { value: 'new-worker' },
    })
    fireEvent.click(screen.getByRole('button', { name: '签发' }))

    await waitFor(() => {
      expect(screen.getByTestId('created-token-next-steps')).toBeTruthy()
    })
    // 下一步三步：复制 → 控制台「Workspace 访问」粘贴 → 「开始领取」。
    const steps = screen.getByTestId('created-token-next-steps')
    expect(steps.textContent).toContain('Workspace 访问')
    expect(steps.textContent).toContain('开始领取')
    expect(steps.textContent).toContain('Worker 控制台要求控制令牌？')
    expect(steps.textContent).toContain(
      '/var/lib/agent-legion-worker-control/control_token'
    )
    expect(
      within(steps).getByTestId('worker-console-link').getAttribute('href')
    ).toBe('http://127.0.0.1:8789')
  })

  it('links a registered worker row to its self-reported console', async () => {
    mockListAgentWorkers.mockResolvedValue([
      { ...sampleWorker, labels: { console_url: 'http://10.0.0.8:8787' } },
      { ...sampleWorker, worker_id: 'w2', name: 'legacy-mac' },
    ])
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('worker-w1')).toBeTruthy()
    })
    expect(
      within(screen.getByTestId('worker-w1'))
        .getByTestId('worker-console-link')
        .getAttribute('href')
    ).toBe('http://10.0.0.8:8787')
    // 旧版 Worker 不自报地址：该行没有入口。
    expect(
      within(screen.getByTestId('worker-w2')).queryByTestId(
        'worker-console-link'
      )
    ).toBeNull()
  })
  it('links the empty registered-worker list to the Worker console', async () => {
    mockListAgentWorkers.mockResolvedValue([])
    renderSection()

    await waitFor(() => {
      expect(screen.getByText(/暂无已注册 Worker/)).toBeTruthy()
    })
    expect(screen.getByTestId('worker-console-link').getAttribute('href')).toBe(
      'http://127.0.0.1:8789'
    )
  })

  it('issues a key pinned to the current workspace (no workspace picker)', async () => {
    mockCreateRegisterToken.mockResolvedValue({
      token_id: 't2',
      register_token: 'plain-secret',
      workspace_id: WORKSPACE_ID,
      label: 'new-worker',
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    renderSection()
    await waitFor(() => screen.getByText('home-mac-mini'))

    // workspace 级设置页：签发表单不再有 workspace 选择器。
    expect(screen.queryByLabelText('workspace 范围')).toBeNull()
    fireEvent.change(screen.getByLabelText('Key 名称'), {
      target: { value: 'new-worker' },
    })
    fireEvent.click(screen.getByRole('button', { name: '签发' }))

    await waitFor(() => {
      expect(screen.getByTestId('created-token')).toBeTruthy()
    })
    expect(mockCreateRegisterToken).toHaveBeenCalledWith({
      label: 'new-worker',
      workspace_id: WORKSPACE_ID,
    })
    // key 是管理对象，token 是它对应的凭证（明文仅展示一次）。
    expect(screen.getByText(/Key「new-worker」已签发/)).toBeTruthy()
    expect(screen.getByText('plain-secret')).toBeTruthy()
    expect(screen.getByText(/仅显示这一次/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '复制 Token' }))
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('plain-secret')
    })
  })

  it('deletes a key after confirming in the dialog', async () => {
    mockDeleteRegisterToken.mockResolvedValue({ token_id: 't1', deleted: true })
    renderSection()
    await waitFor(() => screen.getByText('home-mac-mini'))

    const item = screen.getByTestId('register-token-t1')
    fireEvent.click(item.querySelector('button') as HTMLButtonElement)

    // 项目 MUI dialog（非浏览器原生 confirm）里确认删除。
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('key「home-mac-mini」')
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

    await waitFor(() => {
      expect(mockDeleteRegisterToken).toHaveBeenCalledWith('t1')
    })
  })

  it('offers no worker delete while its bound key is alive', async () => {
    mockListAgentWorkers.mockResolvedValue([
      { ...sampleWorker, register_token_ids: ['t1'] },
    ])
    renderSection()
    await waitFor(() => screen.getByText('mac-mini'))

    const item = screen.getByTestId('worker-w1')
    expect(item.textContent).toContain('绑定 key：home-mac-mini')
    // 没有「吊销 Worker」这类操作；绑定 key 存活时也不提供删除。
    expect(item.querySelector('button')).toBeNull()
  })

  it('deletes a legacy worker without a recorded binding', async () => {
    mockDeleteAgentWorker.mockResolvedValue({ worker_id: 'w1', deleted: true })
    // 无绑定记录的 legacy worker（含 [] allow-all 的旧全局注册，P2 后
    // 本视图可见）随时可手动删；绑定 key 存活的 worker 由删 key 时的
    // 级联自动清理，无手动删除入口。
    renderSection()
    await waitFor(() => screen.getByText('mac-mini'))

    const item = screen.getByTestId('worker-w1')
    const buttons = item.querySelectorAll('button')
    expect(buttons).toHaveLength(1)
    expect(buttons[0].textContent).toBe('删除')
    fireEvent.click(buttons[0])

    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('Worker「mac-mini」')
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

    await waitFor(() => {
      expect(mockDeleteAgentWorker).toHaveBeenCalledWith('w1')
    })
  })

  it('deleting an in-use key names the workers in the dialog', async () => {
    mockListAgentWorkers.mockResolvedValue([
      { ...sampleWorker, register_token_ids: ['t1'] },
    ])
    mockDeleteRegisterToken.mockResolvedValue({ token_id: 't1', deleted: true })
    renderSection()
    await waitFor(() => screen.getByText('mac-mini'))

    // Key 行展示被多少 Worker 的最近注册使用。
    expect(screen.getByTestId('register-token-t1').textContent).toContain(
      '1 个 Worker 使用'
    )

    const item = screen.getByTestId('register-token-t1')
    fireEvent.click(item.querySelector('button') as HTMLButtonElement)

    // dialog 文案点名使用该 key 的 Worker 并说明级联后果。
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('mac-mini')
    expect(dialog.textContent).toContain('一并删除')
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    await waitFor(() => {
      expect(mockDeleteRegisterToken).toHaveBeenCalledWith('t1')
    })
  })

  it('marks a bound-but-legacy-revoked key on the worker row', async () => {
    mockListRegisterTokens.mockResolvedValue([
      { ...sampleToken, revoked: true },
    ])
    mockListAgentWorkers.mockResolvedValue([
      { ...sampleWorker, register_token_ids: ['t1'] },
    ])
    renderSection()
    await waitFor(() => screen.getByText('mac-mini'))

    expect(screen.getByTestId('worker-w1').textContent).toContain(
      '绑定 key：home-mac-mini（已失效）'
    )
  })

  it("renders only this workspace's keys in the worker binding chip (#1141)", async () => {
    mockListRegisterTokens.mockResolvedValue([
      sampleToken,
      {
        ...sampleToken,
        token_id: 't9',
        label: 'other-ws-key',
        workspace_id: 'other_ws',
      },
    ])
    mockListAgentWorkers.mockResolvedValue([
      // 服务两个 workspace 的 worker：绑定 key 同时含本 ws 与其它 ws 的。
      {
        ...sampleWorker,
        allowed_workspaces: [WORKSPACE_ID, 'other_ws'],
        register_token_ids: ['t1', 't9'],
      },
    ])
    renderSection()
    await waitFor(() => screen.getByTestId('worker-w1'))

    const item = screen.getByTestId('worker-w1')
    expect(item.textContent).toContain('绑定 key：home-mac-mini')
    // 其它 workspace 签发的 key 不出现在 chip 正文里。
    expect(item.textContent).not.toContain('other-ws-key')
    // title 仍点名完整绑定（悬停可见其它 workspace 的 key）。
    const bindingChip = item.querySelector(
      'span[title^="该 Worker 最近一次注册使用的 key"]'
    )
    expect(bindingChip?.getAttribute('title')).toContain('other-ws-key')
  })

  it("hides the binding chip when the worker only binds other workspaces' keys (#1141)", async () => {
    mockListRegisterTokens.mockResolvedValue([
      sampleToken,
      {
        ...sampleToken,
        token_id: 't9',
        label: 'other-ws-key',
        workspace_id: 'other_ws',
      },
    ])
    mockListAgentWorkers.mockResolvedValue([
      {
        ...sampleWorker,
        allowed_workspaces: [WORKSPACE_ID, 'other_ws'],
        register_token_ids: ['t9'],
      },
    ])
    renderSection()
    await waitFor(() => screen.getByTestId('worker-w1'))

    const item = screen.getByTestId('worker-w1')
    expect(item.textContent).not.toContain('绑定 key')
    expect(item.textContent).not.toContain('other-ws-key')
    // deletable 判定仍看全量 key 集（#1141 关键坑）：worker 持有其它
    // workspace 的存活 key（t9 在全量集里），手工删除入口保持关闭——
    // 展示过滤不允许打开只有后端知道被 key 挡住的删除路径。
    expect(item.querySelector('button')).toBeNull()
  })

  it('keeps the deletable gate on the full key set while rendering filtered (#1141)', async () => {
    mockListAgentWorkers.mockResolvedValue([
      { ...sampleWorker, register_token_ids: ['t1'] },
    ])
    // t1 不在返回的 token 集合里（已删除）：绑定关系全部失效，可删。
    mockListRegisterTokens.mockResolvedValue([])
    renderSection()
    await waitFor(() => screen.getByTestId('worker-w1'))

    const item = screen.getByTestId('worker-w1')
    // chip 只在还剩本 workspace 的 key 时渲染；全删后不渲染。
    expect(item.textContent).not.toContain('绑定 key：')
    expect(item.querySelectorAll('button')).toHaveLength(1)
  })
})
