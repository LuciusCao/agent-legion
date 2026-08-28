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
  allowed_workspaces: [],
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
        default_workflow_key: '',
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
    expect(mockListAgentWorkers).toHaveBeenCalledWith()
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
    const globalItem = screen.getByTestId('worker-w1')
    expect(globalItem.textContent).toContain('在线')
    expect(globalItem.textContent).toContain('待迁移（旧全局注册）')

    const scopedItem = screen.getByTestId('worker-w2')
    expect(scopedItem.textContent).toContain('离线')
    // workspace 名称优先显示，未知 id 回退为 id 本身。
    expect(scopedItem.textContent).toContain('演示工作区')
    expect(scopedItem.textContent).toContain('demo_workspace')
  })

  it('shows an error when loading fails', async () => {
    mockListRegisterTokens.mockRejectedValue(new Error('HTTP 500'))
    renderSection()

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('HTTP 500')
    })
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
    // 无绑定记录的 legacy worker（含待迁移的旧全局注册）随时可手动删；
    // 绑定 key 存活的 worker 由删 key 时的级联自动清理，无手动删除入口。
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
})
