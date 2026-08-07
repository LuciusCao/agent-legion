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
  workflows: { enabled: true },
  agent_workers: { max_archive_bytes: 104857600, min_protocol_version: 2 },
}

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
  it('loads and renders the fetched values', async () => {
    renderSection()

    expect(await screen.findByLabelText('日志保留天数')).toHaveValue(30)
    expect(screen.getByLabelText('运行目录保留天数')).toHaveValue(7)
    expect(screen.getByLabelText('采样间隔（秒）')).toHaveValue(15)
    expect(screen.getByLabelText('心跳间隔（秒）')).toHaveValue(10)
    expect(screen.getByLabelText('租约 TTL（秒）')).toHaveValue(90)
    expect(screen.getByLabelText('心跳失败阈值')).toHaveValue(3)
    expect(screen.getByLabelText('最低协议版本')).toHaveValue(2)
    expect(screen.getByLabelText('启用 sweeper')).toBeChecked()
    expect(screen.getByLabelText('启用工作流')).toBeChecked()
    expect(screen.getByText(/需重启服务才能生效/)).toBeInTheDocument()
    // Clean form: save stays disabled.
    expect(screen.getByText('保存实例设置')).toBeDisabled()
  })

  it('saves edited values via PUT with integer rounding', async () => {
    vi.mocked(updateInstanceSettings).mockImplementation(async (payload) => ({
      ...payload,
    }))

    renderSection()
    await screen.findByLabelText('日志保留天数')

    fireEvent.change(screen.getByLabelText('日志保留天数'), {
      target: { value: '45.6' },
    })
    fireEvent.change(screen.getByLabelText('心跳间隔（秒）'), {
      target: { value: '12.5' },
    })
    fireEvent.click(screen.getByLabelText('启用工作流'))
    fireEvent.click(screen.getByText('保存实例设置'))

    await waitFor(() => {
      expect(updateInstanceSettings).toHaveBeenCalledWith({
        ...settings,
        cleanup: { ...settings.cleanup, log_retention_days: 46 },
        heartbeat_interval_seconds: 12.5,
        workflows: { enabled: false },
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
    await screen.findByLabelText('日志保留天数')

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
    await screen.findByLabelText('日志保留天数')

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
