import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { ConnectionsSection } from './ConnectionsSection'
import {
  createConnection,
  deleteConnection,
  getConnections,
  getConnectionTypes,
  testConnection,
  updateConnection,
} from '../../api/connections'
import type {
  ConnectionListResponse,
  ConnectionTypesResponse,
} from '../../api/connections'
import { useUiStore } from '../../stores/uiStore'

vi.mock('../../api/connections', () => ({
  getConnections: vi.fn(),
  getConnectionTypes: vi.fn(),
  createConnection: vi.fn(),
  updateConnection: vi.fn(),
  deleteConnection: vi.fn(),
  testConnection: vi.fn(),
}))

// 类型名对齐后端 registry（server/app/services/connection_adapters.py）。
const types: ConnectionTypesResponse = {
  types: [
    {
      type: 'static_bearer',
      description: '静态 Bearer token',
      required_config_keys: [],
      secret_keys: ['token'],
    },
  ],
}

const connections: ConnectionListResponse = {
  connections: [
    {
      key: 'lark-main',
      type: 'static_bearer',
      display_name: '飞书主租户',
      config: { token: { secret_set: true } },
      enabled: true,
      token: {
        expires_at: '2026-08-12T00:00:00Z',
        refreshed_at: '2026-08-11T00:00:00Z',
      },
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-10T00:00:00Z',
    },
    {
      key: 'backup-main',
      type: 'static_bearer',
      display_name: '备用租户',
      config: { token: { secret_set: true } },
      enabled: false,
      token: null,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-10T00:00:00Z',
    },
  ],
}

function renderSection() {
  return render(
    <MemoryRouter>
      <ConnectionsSection />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  window.confirm = vi.fn(() => true)
  vi.mocked(getConnections).mockResolvedValue(connections)
  vi.mocked(getConnectionTypes).mockResolvedValue(types)
})

describe('ConnectionsSection', () => {
  it('renders the connection list with token status', async () => {
    renderSection()

    expect(await screen.findByText('lark-main')).toBeInTheDocument()
    expect(screen.getByText('飞书主租户')).toBeInTheDocument()
    expect(screen.getByText('backup-main')).toBeInTheDocument()
    // token 存在时展示有效期与上次刷新；缺失时展示「未获取」。
    expect(screen.getByText(/有效期至/)).toBeInTheDocument()
    expect(screen.getByText(/上次刷新/)).toBeInTheDocument()
    expect(screen.getByText('未获取')).toBeInTheDocument()
    // enabled 开关反映连接状态。
    expect(screen.getByLabelText('启用 lark-main')).toBeChecked()
    expect(screen.getByLabelText('启用 backup-main')).not.toBeChecked()
  })

  it('creates a connection with parsed JSON config', async () => {
    vi.mocked(createConnection).mockResolvedValue(connections.connections[0])

    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByRole('button', { name: '新建连接' }))
    // secret 键说明来自 connection-types。
    expect(screen.getByText(/secret 键：token/)).toBeInTheDocument()

    fireEvent.change(
      screen.getByLabelText('连接 key（小写字母 / 数字 / 连字符）'),
      { target: { value: 'lark-backup' } }
    )
    fireEvent.change(screen.getByLabelText('显示名'), {
      target: { value: '备用租户' },
    })
    fireEvent.change(screen.getByLabelText('配置 JSON'), {
      target: { value: '{"token": "s3cret"}' },
    })
    fireEvent.click(screen.getByText('创建'))

    await waitFor(() => {
      expect(createConnection).toHaveBeenCalledWith({
        key: 'lark-backup',
        type: 'static_bearer',
        display_name: '备用租户',
        config: { token: 's3cret' },
      })
    })
  })

  it('blocks saving when the config JSON is invalid', async () => {
    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByRole('button', { name: '新建连接' }))
    fireEvent.change(screen.getByLabelText('配置 JSON'), {
      target: { value: '{not-json' },
    })

    expect(
      await screen.findByText('JSON 格式非法，请检查后再保存')
    ).toBeInTheDocument()
    expect(screen.getByText('创建')).toBeDisabled()
    expect(createConnection).not.toHaveBeenCalled()
  })

  it('keeps the edit and create forms mutually exclusive', async () => {
    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByRole('button', { name: '新建连接' }))
    expect(
      screen.getByLabelText('连接 key（小写字母 / 数字 / 连字符）')
    ).toBeInTheDocument()

    // 打开编辑会关掉新建表单，避免重复的 DOM id 与共享 saving 状态。
    fireEvent.click(screen.getByLabelText('编辑 lark-main'))
    expect(screen.getByText('编辑连接 lark-main')).toBeInTheDocument()
    expect(
      screen.queryByLabelText('连接 key（小写字母 / 数字 / 连字符）')
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '新建连接' }))
    expect(screen.queryByText('编辑连接 lark-main')).not.toBeInTheDocument()
    expect(
      screen.getByLabelText('连接 key（小写字母 / 数字 / 连字符）')
    ).toBeInTheDocument()
  })

  it('shows the inline success message when the connection test passes', async () => {
    vi.mocked(testConnection).mockResolvedValue({
      ok: true,
      message: 'token 获取成功',
    })

    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByLabelText('测试 lark-main'))

    await waitFor(() => {
      expect(testConnection).toHaveBeenCalledWith('lark-main')
    })
    expect(
      await screen.findByText('测试成功：token 获取成功')
    ).toBeInTheDocument()
  })

  it('shows the server detail when the connection test fails', async () => {
    vi.mocked(testConnection).mockRejectedValue(
      new Error('HTTP 400: token 无效')
    )

    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByLabelText('测试 lark-main'))

    expect(
      await screen.findByText('测试失败：HTTP 400: token 无效')
    ).toBeInTheDocument()
  })

  it('saves an edited connection via PUT with parsed JSON config', async () => {
    vi.mocked(updateConnection).mockResolvedValue(connections.connections[0])

    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByLabelText('编辑 lark-main'))
    const jsonField = screen.getByLabelText('配置 JSON')
    // 回显包含 secret 掩码，保持不变即不修改。
    expect((jsonField as HTMLTextAreaElement).value).toContain('secret_set')
    fireEvent.change(jsonField, {
      target: { value: '{"token": {"secret_set": true}}' },
    })
    fireEvent.click(screen.getByText('保存'))

    await waitFor(() => {
      expect(updateConnection).toHaveBeenCalledWith('lark-main', {
        display_name: '飞书主租户',
        config: { token: { secret_set: true } },
      })
    })
  })

  it('omits config from the update when only the display name changed', async () => {
    vi.mocked(updateConnection).mockResolvedValue(connections.connections[0])

    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByLabelText('编辑 lark-main'))
    fireEvent.change(screen.getByLabelText('显示名'), {
      target: { value: '新名字' },
    })
    fireEvent.click(screen.getByText('保存'))

    // 不带 config：后端只要 config 非 None 就会清 token 缓存。
    await waitFor(() => {
      expect(updateConnection).toHaveBeenCalledWith('lark-main', {
        display_name: '新名字',
      })
    })
  })

  it('deletes a connection after confirmation', async () => {
    vi.mocked(deleteConnection).mockResolvedValue({
      ok: true,
      message: '已删除',
    })

    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByLabelText('删除 lark-main'))

    await waitFor(() => {
      expect(deleteConnection).toHaveBeenCalledWith('lark-main')
    })
    expect(window.confirm).toHaveBeenCalled()
  })

  it('does not delete when the confirmation is declined', async () => {
    window.confirm = vi.fn(() => false)

    renderSection()
    await screen.findByText('lark-main')

    fireEvent.click(screen.getByLabelText('删除 lark-main'))

    expect(deleteConnection).not.toHaveBeenCalled()
  })

  it('enables a disabled connection via the toggle', async () => {
    vi.mocked(updateConnection).mockResolvedValue({
      ...connections.connections[1],
      enabled: true,
    })

    renderSection()
    await screen.findByText('backup-main')

    fireEvent.click(screen.getByLabelText('启用 backup-main'))

    await waitFor(() => {
      expect(updateConnection).toHaveBeenCalledWith('backup-main', {
        enabled: true,
      })
    })
    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual({
        message: '连接已启用',
        type: 'success',
      })
    })
  })

  it('shows an error toast when the toggle fails', async () => {
    vi.mocked(updateConnection).mockRejectedValue(new Error('HTTP 500: boom'))

    renderSection()
    await screen.findByText('backup-main')

    fireEvent.click(screen.getByLabelText('启用 backup-main'))

    await waitFor(() => {
      expect(useUiStore.getState().toast).toEqual({
        message: 'HTTP 500: boom',
        type: 'error',
      })
    })
  })
})
