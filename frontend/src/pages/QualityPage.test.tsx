import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Routes, Route } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import { TestQueryProvider } from '../testing/testQueryClient'
import { QualityPage } from './QualityPage'

const batch = {
  id: 'b1',
  name: '批次A',
  created_at: '2026-01-01T00:00:00Z',
  created_by: 'admin',
  sample_size: 20,
  seed: 'seed-123',
  workflow_key: 'question_comprehension_info',
  workspace_id: 'ws1',
  filters: {},
}

const item = {
  id: 'i1',
  batch_id: 'b1',
  node_key: 'generate_key_info',
  capability: 'generate_key_info',
  skill_version: 'v1.2.3',
  agent_version: 3,
  agent_definition_hash: 'hash1',
  provider: 'gateway',
  model: 'model-x',
  run_status: 'failed',
  failure_category: 'timeout',
  failure_detail: '',
  job_id: 'job-1',
  node_run_id: 7,
  created_at: '2026-01-01T00:00:00Z',
  current_label: null,
}

const mockFetchSampleBatches = vi.fn().mockResolvedValue({ batches: [batch] })
const mockCreateSampleBatch = vi.fn().mockResolvedValue({
  ...batch,
  id: 'b2',
  name: '批次B',
  sampled_count: 20,
})
const mockFetchSampleBatchDetail = vi.fn().mockResolvedValue({
  batch,
  items: [item],
  total: 1,
})
const mockFetchSampleBatchStats = vi.fn().mockResolvedValue({
  batch_id: 'b1',
  groups: [
    {
      node_key: 'generate_key_info',
      skill_version: 'v1.2.3',
      provider: 'gateway',
      model: 'model-x',
      runs: 5,
      succeeded: 4,
      success_rate: 0.8,
      labeled: 2,
      good: 1,
      bad: 1,
      good_rate: 0.5,
    },
  ],
})
const mockFetchSampleItemDetail = vi.fn().mockResolvedValue({
  item,
  labels: [],
  artifacts: [
    { name: 'key_info_raw.json', content: '{"a":1}', truncated: false },
    { name: 'stderr.log', content: 'boom', truncated: false },
  ],
})
const mockAddSampleItemLabel = vi.fn().mockResolvedValue({
  label: {
    id: 'l1',
    item_id: 'i1',
    verdict: 'bad',
    reason_codes: ['fact_error'],
    note: '',
    labeled_by: 'admin',
    target: 'sample_item',
    created_at: '2026-01-02T00:00:00Z',
  },
})

const replay = {
  id: 'r1',
  item_id: 'i1',
  agent_id: 'agent-1',
  agent_version: 5,
  status: 'succeeded',
  replay_job_id: 'job-r1',
  error_message: '',
  created_at: '2026-01-02T00:00:00Z',
  finished_at: '2026-01-02T00:01:00Z',
  created_by: 'admin',
}
const mockFetchReplays = vi.fn().mockResolvedValue({ replays: [] })
const mockCreateReplay = vi.fn().mockResolvedValue({
  replay: { ...replay, id: 'r2', status: 'pending', finished_at: null },
})
const mockFetchReplayDetail = vi.fn().mockResolvedValue({
  replay,
  labels: [],
  artifacts: [
    { name: 'key_info_raw.json', content: '{"a":2}', truncated: false },
  ],
  input_artifacts: [
    {
      name: 'questions_parsed_lean.json',
      content: '{"q":1}',
      truncated: false,
    },
  ],
})

vi.mock('../api/qualityApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/qualityApi')>()
  return {
    ...actual,
    fetchSampleBatches: (...args: unknown[]) => mockFetchSampleBatches(...args),
    createSampleBatch: (...args: unknown[]) => mockCreateSampleBatch(...args),
    fetchSampleBatchDetail: (...args: unknown[]) =>
      mockFetchSampleBatchDetail(...args),
    fetchSampleBatchStats: (...args: unknown[]) =>
      mockFetchSampleBatchStats(...args),
    fetchSampleItemDetail: (...args: unknown[]) =>
      mockFetchSampleItemDetail(...args),
    addSampleItemLabel: (...args: unknown[]) => mockAddSampleItemLabel(...args),
    fetchReplays: (...args: unknown[]) => mockFetchReplays(...args),
    createReplay: (...args: unknown[]) => mockCreateReplay(...args),
    fetchReplayDetail: (...args: unknown[]) => mockFetchReplayDetail(...args),
  }
})

vi.mock('../api', () => ({
  fetchWorkspaces: vi.fn().mockResolvedValue({
    workspaces: [{ id: 'ws1', name: '测试空间' }],
  }),
}))

function renderPage() {
  return render(
    <TestQueryProvider>
      <MemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        initialEntries={['/workspaces/ws1/quality']}
      >
        <Routes>
          <Route
            path="/workspaces/:workspaceId/quality"
            element={<QualityPage />}
          />
        </Routes>
      </MemoryRouter>
    </TestQueryProvider>
  )
}

describe('QualityPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetchReplays.mockResolvedValue({ replays: [] })
  })

  it('渲染批次列表', async () => {
    renderPage()
    expect(await screen.findByText('测试空间 / 质量闭环')).toBeInTheDocument()
    expect(await screen.findByText('批次A')).toBeInTheDocument()
    expect(screen.getByText('seed-123')).toBeInTheDocument()
  })

  it('新建抽样对话框提交后跳到打标页', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '新建抽样' }))
    await user.type(screen.getByLabelText(/^名称/), '批次B')
    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() =>
      expect(mockCreateSampleBatch).toHaveBeenCalledWith('ws1', {
        name: '批次B',
        sample_size: 20,
        seed: null,
        filters: { node_keys: null, since: null, until: null },
      })
    )
    // 创建成功后自动切到打标 tab 并加载新批次详情
    expect(
      await screen.findByRole('tab', { name: '打标', selected: true })
    ).toBeInTheDocument()
    await waitFor(() =>
      expect(mockFetchSampleBatchDetail).toHaveBeenCalledWith('ws1', 'b2')
    )
    // 创建成功使批次列表失效重取；等这次更新落地再结束，避免 act 警告
    await waitFor(() => expect(mockFetchSampleBatches).toHaveBeenCalledTimes(2))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }, 20000)

  it('打标表单：bad 需选原因码，提交调用 labels API', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '去打标' }))
    // item 详情与产物渲染
    expect(await screen.findByText('样本快照')).toBeInTheDocument()
    expect(screen.getByText('key_info_raw.json')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()

    const submit = screen.getByRole('button', { name: '提交打标' })
    expect(submit).toBeEnabled() // 默认 good，无需原因码
    await user.click(screen.getByLabelText('bad'))
    expect(submit).toBeDisabled()
    await user.click(screen.getByText('fact_error'))
    expect(submit).toBeEnabled()
    await user.click(submit)
    await waitFor(() =>
      expect(mockAddSampleItemLabel).toHaveBeenCalledWith('ws1', 'i1', {
        verdict: 'bad',
        reason_codes: ['fact_error'],
        note: '',
      })
    )
    // 打标成功使样本详情与批次详情失效重取；等这两次更新落地再结束，避免 act 警告
    await waitFor(() =>
      expect(mockFetchSampleItemDetail).toHaveBeenCalledTimes(2)
    )
    await waitFor(() =>
      expect(mockFetchSampleBatchDetail).toHaveBeenCalledTimes(2)
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  })

  it('统计 tab 展示分组指标', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '去打标' }))
    await user.click(await screen.findByRole('tab', { name: '统计' }))
    expect(await screen.findByText('80%（4/5）')).toBeInTheDocument()
    expect(screen.getByText('model-x')).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    // 非 review 节点不渲染混淆矩阵区块
    expect(screen.queryByLabelText('Review 混淆矩阵')).not.toBeInTheDocument()
  })

  it('统计 tab 为 review 节点渲染混淆矩阵', async () => {
    mockFetchSampleBatchStats.mockResolvedValueOnce({
      batch_id: 'b1',
      groups: [
        {
          node_key: 'review_key_info',
          skill_version: 'v1.2.3',
          provider: 'gateway',
          model: 'model-x',
          runs: 6,
          succeeded: 4,
          success_rate: 4 / 6,
          labeled: 4,
          good: 2,
          bad: 2,
          good_rate: 0.5,
          confusion_matrix: {
            tp: 1,
            fp: 1,
            fn: 1,
            tn: 1,
            precision: 0.5,
            recall: 0.5,
            accuracy: 0.5,
          },
        },
        {
          node_key: 'review_possible_errors',
          skill_version: 'v1.0.0',
          provider: 'gateway',
          model: 'model-y',
          runs: 3,
          succeeded: 3,
          success_rate: 1,
          labeled: 0,
          good: 0,
          bad: 0,
          good_rate: null,
          confusion_matrix: null,
        },
      ],
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '去打标' }))
    await user.click(await screen.findByRole('tab', { name: '统计' }))

    expect(await screen.findByText('正确放行')).toBeInTheDocument()
    expect(screen.getByText('漏放')).toBeInTheDocument()
    expect(screen.getByText('误杀')).toBeInTheDocument()
    expect(screen.getByText('正确拦截')).toBeInTheDocument()
    expect(
      screen.getByText('precision 50% · recall 50% · accuracy 50%')
    ).toBeInTheDocument()
    // 未打标的 review 分组显示引导文案
    expect(screen.getByText(/暂无已打标的可分类样本/)).toBeInTheDocument()
  })

  it('发起 replay：携带 agent_version 调用创建接口', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '去打标' }))
    expect(await screen.findByText('样本快照')).toBeInTheDocument()
    expect(screen.getByText('暂无 replay')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Agent 版本'), '5')
    await user.click(screen.getByRole('button', { name: '发起 Replay' }))
    await waitFor(() =>
      expect(mockCreateReplay).toHaveBeenCalledWith('ws1', 'i1', {
        agent_version: 5,
      })
    )
    // 创建成功使 replay 列表失效重取；等更新落地再结束，避免 act 警告
    await waitFor(() => expect(mockFetchReplays).toHaveBeenCalledTimes(2))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  })

  it('replay 列表渲染与新旧产物对比视图', async () => {
    mockFetchReplays.mockResolvedValue({ replays: [replay] })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '去打标' }))
    await user.click(await screen.findByText('v5'))

    const compare = await screen.findByLabelText('新旧产物对比')
    expect(within(compare).getByText('原产物（v3）')).toBeInTheDocument()
    expect(within(compare).getByText('Replay 产物（v5）')).toBeInTheDocument()
    // 同名产物左右并排：左侧原值 1、右侧 replay 新值 2（JsonTree 渲染）
    expect(within(compare).getByText('1')).toBeInTheDocument()
    expect(within(compare).getByText('2')).toBeInTheDocument()
  })

  it('replay 打标提交携带 replay_id', async () => {
    mockFetchReplays.mockResolvedValue({ replays: [replay] })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '去打标' }))
    await user.click(await screen.findByText('v5'))
    expect(await screen.findByLabelText('新旧产物对比')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '提交 Replay 打标' }))
    await waitFor(() =>
      expect(mockAddSampleItemLabel).toHaveBeenCalledWith('ws1', 'i1', {
        verdict: 'good',
        reason_codes: [],
        note: '',
        replay_id: 'r1',
      })
    )
    // 打标成功使 replay 详情失效重取；等更新落地再结束，避免 act 警告
    await waitFor(() => expect(mockFetchReplayDetail).toHaveBeenCalledTimes(2))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  })
})
