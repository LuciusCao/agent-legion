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
  studio_chat_retention_days: 0,
  workflows: { max_items_per_run: 20000, node_code_max_bytes: 65536 },
  agent_workers: {
    max_archive_bytes: 104857600,
    min_protocol_version: 2,
    max_concurrent_result_commits: 16,
    result_commit_batching: true,
    artifact_spot_check_percent: 3,
    artifact_download_presign_ttl_seconds: 3600,
  },
  agent_enqueue: { workers: 48, max_pending: 1024 },
  result_unpack: { workers: 0 },
  result_validate: { workers: 0 },
  agent_claim: { worker_touch_interval_seconds: 30 },
  csp_script_unsafe_inline: false,
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
    // #786：节点代码体积上限随实例设置管理；验收反馈后表单单位改 KB
    // （GET 字节回显换算为 KB），编辑器按同一值提示 KB。
    expect(screen.getByLabelText('节点代码体积上限（KB）')).toHaveValue(64)
    // #509/#554 容量旋钮组：值渲染 + 契约上界（max 属性）+ 组说明。
    expect(screen.getByLabelText('Agent 入队线程数')).toHaveValue(48)
    expect(screen.getByLabelText('Agent 入队线程数')).toHaveAttribute(
      'max',
      '256'
    )
    expect(screen.getByLabelText('Agent 入队排队上限')).toHaveValue(1024)
    expect(screen.getByLabelText('result 解包进程数（0 = 自动）')).toHaveValue(
      0
    )
    expect(
      screen.getByLabelText('result 解包进程数（0 = 自动）')
    ).toHaveAttribute('max', '64')
    // #561：用户视角命名（无 touch / last_seen_at 实现术语）+ 说明文案。
    expect(screen.getByLabelText('Worker 在线标记写入间隔（秒）')).toHaveValue(
      30
    )
    expect(
      screen.getByText(/心跳（每 10 秒）不受此限制，在线状态判定不受影响/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/解包进程池承接完成波的 CPU 解包/)
    ).toBeInTheDocument()
    expect(screen.getByText(/需重启服务才能生效/)).toBeInTheDocument()
    // 每组带一句面向用户的说明（抽查三组，含此前缺失的监控/本地执行组）。
    expect(
      screen.getByText('自动删除过期的运行日志与产物，控制磁盘占用。')
    ).toBeInTheDocument()
    expect(
      screen.getByText('资源占用的采样频率与监控数据保留时长。')
    ).toBeInTheDocument()
    expect(
      screen.getByText(/无远程 worker 时代码节点由宿主本地沙箱执行/)
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
    fireEvent.change(screen.getByLabelText('Agent 入队线程数'), {
      target: { value: '64' },
    })
    // #561：在线标记写入间隔是小数字段（0.5 秒合法，不取整）。
    fireEvent.change(screen.getByLabelText('Worker 在线标记写入间隔（秒）'), {
      target: { value: '7.5' },
    })
    // #786：节点代码体积上限以 KB 编辑，保存时换算回字节随全文档 PUT 上送。
    fireEvent.change(screen.getByLabelText('节点代码体积上限（KB）'), {
      target: { value: '128' },
    })
    fireEvent.click(screen.getByText('保存实例设置'))

    await waitFor(() => {
      expect(updateInstanceSettings).toHaveBeenCalledWith({
        ...updateBase,
        cleanup: { ...settings.cleanup, log_retention_days: 46 },
        heartbeat_interval_seconds: 12.5,
        workflows: { max_items_per_run: 20000, node_code_max_bytes: 131072 },
        agent_enqueue: { workers: 64, max_pending: 1024 },
        agent_claim: { worker_touch_interval_seconds: 7.5 },
      })
    })
    // Baseline updated: the form is clean again after a successful save.
    await waitFor(() => {
      expect(screen.getByText('保存实例设置')).toBeDisabled()
    })
  })

  it('toggles the preview panel CSP compatibility mode online (#989)', async () => {
    vi.mocked(updateInstanceSettings).mockImplementation(async (payload) => ({
      ...settings,
      ...payload,
    }))

    renderSection()
    // 安全组直接可见（非高级参数），默认关闭，说明降低安全性的代价。
    const toggle =
      await screen.findByLabelText('预览面板兼容模式（允许内联事件属性）')
    expect(toggle).not.toBeChecked()
    expect(screen.getByText(/会降低平台页面的脚本防护/)).toBeInTheDocument()
    fireEvent.click(toggle)
    fireEvent.click(screen.getByText('保存实例设置'))

    await waitFor(() => {
      expect(updateInstanceSettings).toHaveBeenCalledWith({
        ...updateBase,
        csp_script_unsafe_inline: true,
      })
    })
  })

  it('edits the Studio chat retention window online (#1041)', async () => {
    vi.mocked(updateInstanceSettings).mockImplementation(async (payload) => ({
      ...settings,
      ...payload,
    }))

    renderSection()
    // 保留策略组直接可见（非高级参数）：默认 0 = 永不清理。
    const field =
      await screen.findByLabelText('归档/已删除对话保留天数（0 关闭）')
    expect(field).toHaveValue(0)
    expect(field).toHaveAttribute('max', '36500')
    // 开启即首轮清理存量超龄会话（含界面不可见的已删除会话）的警示。
    expect(
      screen.getByText(/首轮清理会删除已超龄的归档\/已删除会话/)
    ).toBeInTheDocument()
    fireEvent.change(field, { target: { value: '30' } })
    fireEvent.click(screen.getByText('保存实例设置'))

    await waitFor(() => {
      expect(updateInstanceSettings).toHaveBeenCalledWith({
        ...updateBase,
        studio_chat_retention_days: 30,
      })
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

  it('rejects node code budget below the 1 KB contract floor before saving', async () => {
    renderSection()
    fireEvent.click(await screen.findByRole('button', { name: '展开高级参数' }))

    // #786 codex P2 + 验收反馈（KB 单位）：0 KB 由客户端拦截（不发请求），
    // 不再落到后端 422 的无指向性报错；下界 1 KB 对应契约 ge=1024 字节。
    expect(screen.getByLabelText('节点代码体积上限（KB）')).toHaveAttribute(
      'min',
      '1'
    )
    fireEvent.change(screen.getByLabelText('节点代码体积上限（KB）'), {
      target: { value: '0' },
    })
    fireEvent.click(screen.getByText('保存实例设置'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '节点代码体积上限（KB） 必须是大于 0 的数字'
    )
    expect(updateInstanceSettings).not.toHaveBeenCalled()
  })

  it('rejects presign TTL outside the contract range before saving', async () => {
    renderSection()
    fireEvent.click(await screen.findByRole('button', { name: '展开高级参数' }))

    // #739 codex P2：契约 ge=60 / le=604800 在客户端拦截（上下界同权）。
    const ttl = screen.getByLabelText('外部产物下载直连 URL 有效期（秒）')
    expect(ttl).toHaveAttribute('min', '60')
    expect(ttl).toHaveAttribute('max', '604800')

    fireEvent.change(ttl, { target: { value: '59' } })
    fireEvent.click(screen.getByText('保存实例设置'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '外部产物下载直连 URL 有效期（秒） 必须不小于 60'
    )
    expect(updateInstanceSettings).not.toHaveBeenCalled()

    fireEvent.change(ttl, { target: { value: '604801' } })
    fireEvent.click(screen.getByText('保存实例设置'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      '外部产物下载直连 URL 有效期（秒） 必须不大于 604800'
    )
    expect(updateInstanceSettings).not.toHaveBeenCalled()

    // 边界接受侧：60 秒与 7 天均为合法值，照常提交。
    fireEvent.change(ttl, { target: { value: '60' } })
    fireEvent.click(screen.getByText('保存实例设置'))
    await waitFor(() => expect(updateInstanceSettings).toHaveBeenCalled())
  })

  it('rejects spot-check percent above 100 before saving', async () => {
    renderSection()
    fireEvent.click(await screen.findByRole('button', { name: '展开高级参数' }))

    fireEvent.change(
      screen.getByLabelText('产物校验抽检比例 %（0 全信任，100 全核验）'),
      {
        target: { value: '101' },
      }
    )
    fireEvent.click(screen.getByText('保存实例设置'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '产物校验抽检比例 %（0 全信任，100 全核验） 必须不大于 100'
    )
    expect(updateInstanceSettings).not.toHaveBeenCalled()
  })

  it('shows the load error when GET fails', async () => {
    vi.mocked(getInstanceSettings).mockRejectedValue(new Error('HTTP 403'))

    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403')
  })
})
