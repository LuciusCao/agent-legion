import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { InstanceSettingsSection } from './InstanceSettingsSection'
import {
  getInstanceSettings,
  updateInstanceSettings,
} from '../../api/instanceSettings'
import type { InstanceSettingsResponse } from '../../api/instanceSettings'

vi.mock('../../api/instanceSettings', () => ({
  getInstanceSettings: vi.fn(),
  updateInstanceSettings: vi.fn(),
}))

const settings: InstanceSettingsResponse = {
  cleanup: {
    log_retention_days: 30,
    run_dir_retention_days: 7,
    interval_seconds: 3600,
  },
  monitoring: { sample_interval_seconds: 15, retention_days: 30 },
  heartbeat_interval_seconds: 10,
  lease_ttl_seconds: 90,
  heartbeat_failure_threshold: 3,
  sweeper_enabled: true,
  sweeper_interval_seconds: 60,
  code_capacity: 16,
  materials_ttl_days: 0,
  execution_retention_days: 0,
  workflows: { max_items_per_run: 20000 },
  agent_workers: { max_archive_bytes: 104857600, min_protocol_version: 2 },
  skills_root: '~/.agents/skills',
}

// PUT 载荷不含只读字段 skills_root。
const updateBase: Record<string, unknown> = { ...settings }
delete updateBase.skills_root

function renderSection() {
  return render(
    <MemoryRouter>
      <InstanceSettingsSection />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getInstanceSettings).mockResolvedValue(settings)
})

describe('InstanceSettingsSection', () => {
  it('keeps advanced groups collapsed by default, shows retention groups', async () => {
    renderSection()

    // 保留策略组（业务参数）直接可见，热读生效的字段级 hint 与 max 上界都在。
    expect(await screen.findByLabelText('材料保留天数（0 关闭）')).toHaveValue(
      0
    )
    expect(screen.getByLabelText('材料保留天数（0 关闭）')).toHaveAttribute(
      'max',
      '36500'
    )
    expect(screen.getByText('保存后立即生效，无需重启')).toBeInTheDocument()
    // 执行面保留（#354）与材料字段同款钉法：值、0 关闭语义的上界、热读 hint。
    expect(screen.getByLabelText('执行记录保留天数（0 关闭）')).toHaveValue(0)
    expect(screen.getByLabelText('执行记录保留天数（0 关闭）')).toHaveAttribute(
      'max',
      '36500'
    )
    expect(
      screen.getByText(
        '终态执行行（请求/租约/用量）按窗口删除；0 为不删除，保存后立即生效'
      )
    ).toBeInTheDocument()
    // 高级参数默认折叠：调优字段不出现在文档中。
    expect(screen.queryByLabelText('日志保留天数')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '展开高级参数' })
    ).toHaveAttribute('aria-expanded', 'false')
    // 顶部说明与只读的 Skill 根目录行照常展示。
    expect(screen.getByText(/默认值适用于绝大多数部署/)).toBeInTheDocument()
    expect(screen.getByText('Skill 根目录')).toBeInTheDocument()
    expect(screen.getByText('~/.agents/skills')).toBeInTheDocument()
    // Clean form: save stays disabled.
    expect(screen.getByText('保存实例设置')).toBeDisabled()
  })

  it('expands advanced groups on demand and renders their values', async () => {
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: '展开高级参数' }))

    expect(screen.getByLabelText('日志保留天数')).toHaveValue(30)
    expect(screen.getByLabelText('运行目录保留天数')).toHaveValue(7)
    expect(screen.getByLabelText('采样间隔（秒）')).toHaveValue(15)
    expect(screen.getByLabelText('心跳间隔（秒）')).toHaveValue(10)
    expect(screen.getByLabelText('租约 TTL（秒）')).toHaveValue(90)
    expect(screen.getByLabelText('心跳失败阈值')).toHaveValue(3)
    expect(screen.getByLabelText('最低协议版本')).toHaveValue(2)
    expect(screen.getByLabelText('启用 sweeper')).toBeChecked()
    expect(screen.getByLabelText('单次 run 条目上限（0 不限制）')).toHaveValue(
      20000
    )
    expect(screen.getByText(/需重启服务才能生效/)).toBeInTheDocument()
    // 每组带一句面向用户的说明（抽查三组，含此前缺失的监控/本地执行组）。
    expect(
      screen.getByText('自动删除过期的运行日志与产物，控制磁盘占用。')
    ).toBeInTheDocument()
    expect(
      screen.getByText('资源占用的采样频率与监控数据保留时长。')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/无远程 worker 可用时，代码节点由宿主本地沙箱执行/)
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '收起高级参数' })
    ).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(screen.getByRole('button', { name: '收起高级参数' }))
    expect(screen.queryByLabelText('日志保留天数')).not.toBeInTheDocument()
  })

  it('saves edited values via PUT with integer rounding', async () => {
    vi.mocked(updateInstanceSettings).mockImplementation(async (payload) => ({
      ...settings,
      ...payload,
    }))

    renderSection()
    fireEvent.click(await screen.findByRole('button', { name: '展开高级参数' }))

    fireEvent.change(screen.getByLabelText('日志保留天数'), {
      target: { value: '45.6' },
    })
    fireEvent.change(screen.getByLabelText('心跳间隔（秒）'), {
      target: { value: '12.5' },
    })
    fireEvent.click(screen.getByText('保存实例设置'))

    await waitFor(() => {
      expect(updateInstanceSettings).toHaveBeenCalledWith({
        ...updateBase,
        cleanup: { ...settings.cleanup, log_retention_days: 46 },
        heartbeat_interval_seconds: 12.5,
        workflows: { max_items_per_run: 20000 },
      })
    })
    // Baseline updated: the form is clean again after a successful save.
    await waitFor(() => {
      expect(screen.getByText('保存实例设置')).toBeDisabled()
    })
  })

  it('shows the server error when PUT fails', async () => {
    vi.mocked(updateInstanceSettings).mockRejectedValue(
      new Error('HTTP 422: validation error')
    )

    renderSection()
    fireEvent.click(await screen.findByRole('button', { name: '展开高级参数' }))

    fireEvent.change(screen.getByLabelText('日志保留天数'), {
      target: { value: '45' },
    })
    fireEvent.click(screen.getByText('保存实例设置'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'HTTP 422: validation error'
    )
  })

  it('rejects non-positive numbers before saving', async () => {
    renderSection()
    fireEvent.click(await screen.findByRole('button', { name: '展开高级参数' }))

    fireEvent.change(screen.getByLabelText('日志保留天数'), {
      target: { value: '-1' },
    })
    fireEvent.click(screen.getByText('保存实例设置'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '日志保留天数 必须是大于 0 的数字'
    )
    expect(updateInstanceSettings).not.toHaveBeenCalled()
  })

  it('shows the load error when GET fails', async () => {
    vi.mocked(getInstanceSettings).mockRejectedValue(new Error('HTTP 403'))

    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403')
  })
})
