import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useUiStore } from '../../stores/uiStore'
import {
  createCampaign,
  createCampaignFromManifest,
  createSubmitCampaign,
  previewCampaign,
} from '../../api/campaignApi'
import { BatchCreateWizard } from './BatchCreateWizard'
import { makeCampaign } from './testHelpers'

// 「新建批量任务」三步向导的组件测试（mock transport）：做什么 → 选目标 →
// 确认 的步骤推进；submit 粘贴/上传双通道；rerun filter/ids 形态；试算
// （upload 通道禁用）与创建载荷（含 name）。

vi.mock('../../api/campaignApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/campaignApi')>()
  return {
    ...actual,
    createCampaign: vi.fn(),
    createSubmitCampaign: vi.fn(),
    createCampaignFromManifest: vi.fn(),
    previewCampaign: vi.fn(),
  }
})

const mockCreateCampaign = vi.mocked(createCampaign)
const mockCreateSubmitCampaign = vi.mocked(createSubmitCampaign)
const mockCreateCampaignFromManifest = vi.mocked(createCampaignFromManifest)
const mockPreviewCampaign = vi.mocked(previewCampaign)

const createdResponse = { campaign: makeCampaign() }

// MUI TextField 把 data-testid 落在根元素上，change 事件要打到内层
// textarea/input。
function field(testId: string): HTMLElement {
  const root = screen.getByTestId(testId)
  if (root instanceof HTMLInputElement || root instanceof HTMLTextAreaElement) {
    return root
  }
  const input = root.querySelector('textarea, input')
  if (!input) throw new Error(`no input inside ${testId}`)
  return input as HTMLElement
}

// MUI TextField select：data-testid 落在根 div 上，打开菜单要打内层
// combobox（JobFilterBar 测试的同款形态）。
async function selectField(testId: string, label: string) {
  const combobox = screen
    .getByTestId(testId)
    .querySelector('[role="combobox"]') as HTMLElement
  await act(async () => {
    fireEvent.mouseDown(combobox)
  })
  await act(async () => {
    fireEvent.click(screen.getByRole('option', { name: label }))
  })
}

function renderWizard(
  props: Partial<Parameters<typeof BatchCreateWizard>[0]> = {}
) {
  return render(
    <TestQueryProvider>
      <BatchCreateWizard
        open
        workspaceId="ws1"
        onClose={vi.fn()}
        onCreated={vi.fn()}
        {...props}
      />
    </TestQueryProvider>
  )
}

/** 断言当前激活的步骤标签（Stepper 的 Mui-active class）。 */
async function expectActiveStep(label: string) {
  await waitFor(() => {
    const active = document.querySelector('.MuiStepLabel-label.Mui-active')
    expect(active?.textContent).toBe(label)
  })
}

/** 直达第 2 步（选目标）。 */
async function toTargetStep() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('batch-wizard-next'))
  })
  await expectActiveStep('选目标')
}

/** 从第 2 步（选目标）直达第 3 步（确认）。 */
async function toConfirmStep() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('batch-wizard-next'))
  })
  await waitFor(() =>
    expect(screen.getByTestId('batch-wizard-create')).toBeInTheDocument()
  )
}

describe('BatchCreateWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCreateCampaign.mockResolvedValue(createdResponse)
    mockCreateSubmitCampaign.mockResolvedValue(createdResponse)
    mockCreateCampaignFromManifest.mockResolvedValue(createdResponse)
    useUiStore.setState({ toast: null })
  })

  it('renders nothing when not open', () => {
    const { container } = renderWizard({ open: false })
    expect(container.firstChild).toBeNull()
  })

  it('advances through the three steps and back', async () => {
    renderWizard()
    await expectActiveStep('做什么')
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-connection-key'), {
        target: { value: 'cms' },
      })
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: 'Q-1001' },
      })
    })
    await toConfirmStep()
    await expectActiveStep('确认')
    await act(async () => {
      fireEvent.click(screen.getByText('上一步'))
    })
    await expectActiveStep('选目标')
    expect(screen.getByTestId('batch-wizard-items-input')).toBeInTheDocument()
  })

  it('requires a target before advancing from step 2', async () => {
    renderWizard()
    await toTargetStep()
    expect(screen.getByTestId('batch-wizard-next')).toBeDisabled()
  })

  it('creates a submit batch from pasted ids with the name', async () => {
    renderWizard()
    await act(async () => {
      fireEvent.change(field('batch-wizard-name'), {
        target: { value: '添加 · 开学季补录' },
      })
    })
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-connection-key'), {
        target: { value: 'cms' },
      })
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: 'Q-1001\nQ-1002' },
      })
    })
    expect(screen.getByText(/已解析 2 条/)).toBeInTheDocument()
    await toConfirmStep()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      expect(mockCreateSubmitCampaign).toHaveBeenCalledWith(
        'ws1',
        {
          items: [
            { type: 'ref', connection_key: 'cms', external_id: 'Q-1001' },
            { type: 'ref', connection_key: 'cms', external_id: 'Q-1002' },
          ],
        },
        '添加 · 开学季补录'
      )
    })
  })

  it('creates a submit batch from pasted jsonl object lines', async () => {
    renderWizard()
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: {
          value:
            '{"type": "ref", "connection_key": "cms", "external_id": "a1"}',
        },
      })
    })
    await toConfirmStep()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      expect(mockCreateSubmitCampaign).toHaveBeenCalledWith(
        'ws1',
        {
          items: [{ type: 'ref', connection_key: 'cms', external_id: 'a1' }],
        },
        ''
      )
    })
  })

  it('flags invalid jsonl lines without enabling create', async () => {
    renderWizard()
    await toTargetStep()
    // 对象行解析失败（{ 开头但不是合法 JSON）报行号并卡住「下一步」。
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: '{"type": "ref", "external_id":' },
      })
    })
    expect(screen.getByText(/第 1 行不是合法 JSON/)).toBeInTheDocument()
    expect(screen.getByTestId('batch-wizard-next')).toBeDisabled()
  })

  it('requires a connection key for pasted plain ids', async () => {
    // 审核 P2 回归锁：纯 ID 行共享连接 Key，为空时后端必拒（连接标识
    // 最短 1 字符）——必须卡住「下一步」并给出中文提示，不能等创建报错。
    renderWizard()
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: 'Q-1001' },
      })
    })
    expect(
      screen.getByText(/需要先填写连接 Key 才能关联外部数据源/)
    ).toBeInTheDocument()
    expect(screen.getByTestId('batch-wizard-next')).toBeDisabled()

    // 补上连接 Key 后即可推进。
    await act(async () => {
      fireEvent.change(field('batch-wizard-connection-key'), {
        target: { value: 'cms' },
      })
    })
    expect(screen.getByTestId('batch-wizard-next')).toBeEnabled()
    await toConfirmStep()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      expect(mockCreateSubmitCampaign).toHaveBeenCalledWith(
        'ws1',
        {
          items: [
            { type: 'ref', connection_key: 'cms', external_id: 'Q-1001' },
          ],
        },
        ''
      )
    })
  })

  it('exempts jsonl object lines from the connection key requirement', async () => {
    // 对象行自带连接信息（审核 P2）：不填连接 Key 也能推进（创建路径的
    // 载荷已在 jsonl 对象行用例覆盖，这里只锁「能推进」语义）。
    renderWizard()
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: {
          value:
            '{"type": "ref", "connection_key": "cms", "external_id": "a1"}',
        },
      })
    })
    expect(screen.queryByText(/需要先填写连接 Key/)).not.toBeInTheDocument()
    await toConfirmStep()
  })

  it('discards a stale preview after the inputs change', async () => {
    // 审核 P2 回归锁：试算成功后回到第 2 步改条件（模式 / 筛选 / 批参数
    // 都塑形请求），再进确认页不得展示按旧条件算出的数量。
    renderWizard()
    await selectField('batch-wizard-mode', '重跑任务')
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-filter-input'), {
        target: { value: '{"status": "failed"}' },
      })
    })
    await toConfirmStep()
    mockPreviewCampaign.mockResolvedValueOnce({
      result: {
        mode: 'rerun',
        total_count: 100,
        eligible_count: 40,
        estimated_batches: 1,
        batch_size: 5000,
      },
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-preview-button'))
    })
    await waitFor(() => {
      expect(screen.getByText(/试算结果：匹配 100 个任务/)).toBeInTheDocument()
    })

    // 回第 2 步改筛选（缩小范围），再进确认页：旧试算结果必须已失效。
    await act(async () => {
      fireEvent.click(screen.getByText('上一步'))
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-filter-input'), {
        target: { value: '{"status": "failed", "workflow_version": 2}' },
      })
    })
    await toConfirmStep()
    expect(
      screen.queryByText(/试算结果：匹配 100 个任务/)
    ).not.toBeInTheDocument()
    expect(screen.getByText(/可先试算确认数量，再创建/)).toBeInTheDocument()
  })

  it('ignores a preview response that arrives after the inputs changed', async () => {
    // 在途旧响应（审核 P2）：试算发出后立刻改输入，慢回来的旧结果既不
    // 展示为当前数量、也不会在「改回来」时复活。
    renderWizard()
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-connection-key'), {
        target: { value: 'cms' },
      })
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: 'Q-1001\nQ-1002' },
      })
    })
    await toConfirmStep()
    let resolvePreview!: (value: unknown) => void
    mockPreviewCampaign.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve
      }) as never
    )
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-preview-button'))
    })

    // 试算在途时回第 2 步追加 ID（请求已过期）。
    await act(async () => {
      fireEvent.click(screen.getByText('上一步'))
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: 'Q-1001\nQ-1002\nQ-1003' },
      })
    })
    await toConfirmStep()
    await act(async () => {
      resolvePreview({
        result: {
          mode: 'submit',
          total_items: 2,
          would_create: 2,
          would_skip: 0,
          estimated_batches: 1,
          batch_size: 5000,
        },
      })
    })
    // 过期响应被丢弃：确认页回到「可先试算」基线，而不是旧的两条结果。
    await waitFor(() => {
      expect(screen.getByText(/可先试算确认数量，再创建/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/试算结果：共 2 条/)).not.toBeInTheDocument()
  })

  it('previews then creates; the upload channel disables the preview', async () => {
    renderWizard()
    await toTargetStep()
    // 行内通道：试算可用（纯 ID 行需要连接 Key）。
    await act(async () => {
      fireEvent.change(field('batch-wizard-connection-key'), {
        target: { value: 'cms' },
      })
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: 'Q-1001' },
      })
    })
    await toConfirmStep()
    mockPreviewCampaign.mockResolvedValueOnce({
      result: {
        mode: 'submit',
        total_items: 1,
        would_create: 1,
        would_skip: 0,
        estimated_batches: 1,
        batch_size: 5000,
      },
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-preview-button'))
    })
    await waitFor(() => {
      expect(mockPreviewCampaign).toHaveBeenCalledWith('ws1', {
        mode: 'submit',
        name: '',
        submit: {
          items: [
            { type: 'ref', connection_key: 'cms', external_id: 'Q-1001' },
          ],
        },
      })
    })
    expect(screen.getByText(/试算结果：共 1 条/)).toBeInTheDocument()

    // 切回第 2 步换 upload 通道（P3：multipart 无 dry-run）。
    await act(async () => {
      fireEvent.click(screen.getByText('上一步'))
    })
    await expectActiveStep('选目标')
    await selectField('batch-wizard-channel', '上传清单文件（.jsonl / .csv）')
    const file = new File(
      ['{"type":"ref","connection_key":"cms","external_id":"a1"}'],
      'm.jsonl',
      { type: 'application/x-ndjson' }
    )
    await act(async () => {
      fireEvent.change(field('batch-wizard-manifest-input'), {
        target: { files: [file] },
      })
    })
    expect(screen.getByText(/已选择 m.jsonl/)).toBeInTheDocument()
    await toConfirmStep()
    // 确认步骤的试算按钮对 upload 通道禁用（服务端创建时统一规整校验）。
    expect(screen.getByTestId('batch-wizard-preview-button')).toBeDisabled()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      // name 为空不落表单字段（服务端缺省即空串，由前端派生默认名）。
      expect(mockCreateCampaignFromManifest).toHaveBeenCalledWith(
        'ws1',
        file,
        {}
      )
    })
  })

  it('creates a rerun batch from a filter with node selection', async () => {
    renderWizard()
    await selectField('batch-wizard-mode', '重跑任务')
    await toTargetStep()
    // 默认按筛选全量：粘贴 filter JSON；「从失败节点重跑」默认勾选。
    await act(async () => {
      fireEvent.change(field('batch-wizard-filter-input'), {
        target: { value: '{"status": "failed"}' },
      })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/从失败节点重跑/))
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-nodekey-input'), {
        target: { value: 'extract' },
      })
    })
    await toConfirmStep()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      expect(mockCreateCampaign).toHaveBeenCalledWith(
        'ws1',
        'rerun',
        expect.objectContaining({
          from_failed_node: false,
          node_key: 'extract',
          filter: { status: 'failed' },
        }),
        ''
      )
    })
  })

  it('creates a rerun batch with explicit job ids', async () => {
    renderWizard()
    await selectField('batch-wizard-mode', '重跑任务')
    await toTargetStep()
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/按筛选条件全量/))
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-jobids-input'), {
        target: { value: 'j1\nj2' },
      })
    })
    // 从失败节点默认勾选：ids 形态同样合法（from_failed_node=true）。
    await toConfirmStep()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      expect(mockCreateCampaign).toHaveBeenCalledWith(
        'ws1',
        'rerun',
        expect.objectContaining({
          from_failed_node: true,
          job_ids: ['j1', 'j2'],
        }),
        ''
      )
    })
  })

  it('creates an upgrade batch without node selection', async () => {
    renderWizard()
    await selectField('batch-wizard-mode', '升级任务')
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-filter-input'), {
        target: { value: '{"workflow_version": 1}' },
      })
    })
    await toConfirmStep()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      expect(mockCreateCampaign).toHaveBeenCalledWith(
        'ws1',
        'upgrade',
        expect.objectContaining({
          from_failed_node: false,
          filter: { workflow_version: 1 },
        }),
        ''
      )
    })
  })

  it('passes knob overrides through to the create payload', async () => {
    renderWizard()
    await toTargetStep()
    await act(async () => {
      fireEvent.change(field('batch-wizard-connection-key'), {
        target: { value: 'cms' },
      })
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-items-input'), {
        target: { value: 'Q-1001' },
      })
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-watermark-input'), {
        target: { value: '1000' },
      })
    })
    await act(async () => {
      fireEvent.change(field('batch-wizard-batchsize-input'), {
        target: { value: '500' },
      })
    })
    await toConfirmStep()
    await act(async () => {
      fireEvent.click(screen.getByTestId('batch-wizard-create'))
    })
    await waitFor(() => {
      expect(mockCreateSubmitCampaign).toHaveBeenCalledWith(
        'ws1',
        expect.objectContaining({ watermark: 1000, batch_size: 500 }),
        ''
      )
    })
  })
})
