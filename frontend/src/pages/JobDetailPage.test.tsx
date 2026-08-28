import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  act,
} from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { MemoryRouter } from '../testing/TestMemoryRouter'
import JobDetailPage from './JobDetailPage'
import { useUiStore } from '../stores/uiStore'

const mockDetail = {
  job: {
    id: 'j1',
    workspace_id: 'ws1',
    workflow_key: 'question_content',
    source_id: 'Q100',
    source_type: 'knowledge',
    title: 'Algebra Problem',
    status: 'running',
    created_at: '2026-06-09T07:59:00Z',
    updated_at: '2026-06-09T08:00:00Z',
  },
  nodes: [
    {
      id: 1,
      job_id: 'j1',
      node_key: 'extract',
      label: '提取',
      status: 'completed',
      capability: 'extract',
      executor_id: 'code-default',
      executor_kind: 'code',
      after: [],
      inputs: [],
      outputs: [],
      started_at: '2026-06-09T08:00:00Z',
      finished_at: '2026-06-09T08:00:12Z',
      error_message: '',
    },
    {
      id: 2,
      job_id: 'j1',
      node_key: 'generate',
      label: '生成',
      status: 'running',
      capability: 'generate',
      executor_id: 'pi',
      executor_kind: 'pi',
      after: ['extract'],
      inputs: [],
      outputs: [],
      started_at: '2026-06-09T08:00:13Z',
      error_message: '',
    },
    {
      id: 3,
      job_id: 'j1',
      node_key: 'review',
      label: '审核',
      status: 'stale',
      capability: 'review',
      executor_id: 'openclaw-default',
      executor_kind: 'openclaw',
      after: ['generate'],
      inputs: [],
      outputs: [],
      error_message: '',
    },
  ],
  runs: [
    {
      id: 1,
      job_id: 'j1',
      node_key: 'extract',
      status: 'completed',
      started_at: '2026-06-09T08:00:00Z',
      finished_at: '2026-06-09T08:00:12Z',
      command_json: '[]',
      exit_code: 0,
      log_path: '',
      error_message: '',
    },
  ],
  artifacts: ['question.json'],
}

// JobDetailPage injects app-bar actions into useUiStore, but WorkspaceLayout/AppBar
// is not rendered in this isolated test, so ActionRenderer renders the stored actions
// so tests can interact with them.
function ActionRenderer() {
  const actions = useUiStore((state) => state.detailPageActions)
  return <div data-testid="detail-actions-host">{actions}</div>
}

function renderPage(initialEntry = '/workspaces/ws1/jobs/j1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ActionRenderer />
      <Routes>
        <Route
          path="/workspaces/:workspaceId/jobs/:jobId"
          element={<JobDetailPage />}
        />
        <Route
          path="/workspaces/:workspaceId"
          element={<div data-testid="workspace-main-page">Workspace Main</div>}
        />
      </Routes>
    </MemoryRouter>
  )
}

/** 只数 /api/jobs/j1 的 detail GET（产物 fetch 等其他请求不计入轮询断言）。 */
function detailGetCalls(fetchMock: ReturnType<typeof createFetchMock>): number {
  return fetchMock.mock.calls.filter(
    ([url, init]) => url === '/api/jobs/j1' && (init?.method ?? 'GET') === 'GET'
  ).length
}

function createFetchMock(
  overrides: {
    detailStatus?: string
    packageUrl?: string | null
    pauseReason?: string | null
  } = {}
) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url === '/api/jobs/j1' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          ...mockDetail,
          job: {
            ...mockDetail.job,
            status: overrides.detailStatus ?? 'running',
            execution_control:
              overrides.pauseReason != null
                ? {
                    paused: true,
                    pause_reason: overrides.pauseReason,
                    target_node_key: 'review',
                    mode: 'until_node',
                  }
                : undefined,
          },
        }),
      })
    }
    if (url === '/api/jobs/j1' && method === 'DELETE') {
      return Promise.resolve({
        ok: true,
        json: async () => ({ deleted: 'j1' }),
      })
    }
    if (
      url.startsWith('/api/jobs/j1/nodes/') &&
      url.endsWith('/rerun') &&
      method === 'POST'
    ) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          operation: 'rerun',
          status: 'succeeded',
        }),
      })
    }
    if (url === '/api/jobs/j1/run-to' && method === 'POST') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          operation: 'run_to',
          status: 'succeeded',
        }),
      })
    }
    if (url === '/api/jobs/j1/continue' && method === 'POST') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          operation: 'continue',
          status: 'succeeded',
        }),
      })
    }
    if (url === '/api/workspaces/ws1/jobs/package' && method === 'POST') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          download_url:
            overrides.packageUrl ?? '/api/workspaces/ws1/packages/pkg.zip',
          package_filename: 'pkg.zip',
          succeeded_count: 1,
          failed_count: 0,
          results: [{ job_id: 'j1', status: 'succeeded' }],
        }),
      })
    }
    if (url === '/api/jobs/j1/runs/1/token-usage' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          run_id: 1,
          usage: null,
          reason: 'no token usage recorded for run',
        }),
      })
    }
    if (url === '/api/jobs/j1/token-usage' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          job_id: 'j1',
          currency: 'CNY',
          runs: [],
          total: {
            message_count: 0,
            input_tokens: 0,
            output_tokens: 0,
            cache_read_tokens: 0,
            total_tokens: 0,
            cost: {
              input: 0,
              output: 0,
              cache_read: 0,
              total: 0,
              currency: 'CNY',
            },
            pricing_missing: false,
          },
          runs_with_usage: 0,
          runs_without_usage: 0,
        }),
      })
    }
    if (url === '/api/jobs/j1/artifacts/question.json' && method === 'GET') {
      return Promise.resolve({
        ok: true,
        json: async () => ({ name: 'question.json', content: '{}' }),
      })
    }
    return Promise.resolve({ ok: true, json: async () => ({}) })
  })
}

describe('JobDetailPage', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    useUiStore.setState({ tokenUsageDialogOpen: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    cleanup()
  })

  it('renders page with job detail', async () => {
    vi.stubGlobal('fetch', createFetchMock())

    renderPage()

    expect(await screen.findByText('提取')).toBeInTheDocument()
    expect(screen.getAllByText('提取').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('生成').length).toBeGreaterThanOrEqual(1)
  })

  it('opens fullscreen DAG dialog from progress panel', async () => {
    vi.stubGlobal('fetch', createFetchMock())

    const { container } = renderPage()
    await waitFor(() => {
      expect(screen.getByLabelText('查看 DAG')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByLabelText('查看 DAG'))
    expect(await screen.findByLabelText('关闭')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-testid="dag-node"]')).toHaveLength(
      3
    )
    expect(
      container.querySelector('[data-testid="dag-node"][data-status="stale"]')
    ).toBeInTheDocument()
    expect(screen.getAllByText('提取').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('生成').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('审核').length).toBeGreaterThanOrEqual(1)
  })

  it('polls detail while job is running', async () => {
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(detailGetCalls(fetchMock)).toBe(1)

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    // +1 detail poll；mount 时的 workers invalidate 在无活跃观察者时不产生请求。
    // 通用产物预览的 artifact fetch 不计入（issue #11 后属正常行为）。
    expect(detailGetCalls(fetchMock)).toBe(2)
  })

  it.each([['running'], ['queued']] as const)(
    'polls %s jobs every five seconds',
    async (detailStatus) => {
      const fetchMock = createFetchMock({ detailStatus })
      vi.stubGlobal('fetch', fetchMock)

      const { unmount } = renderPage()
      await waitFor(() => {
        expect(screen.getByText('提取')).toBeInTheDocument()
      })

      expect(detailGetCalls(fetchMock)).toBe(1)

      await act(async () => {
        vi.advanceTimersByTime(5000)
      })
      // +1 detail poll；mount 时的 workers invalidate 在无活跃观察者时不产生请求。
      // 通用产物预览的 artifact fetch 不计入（issue #11 后属正常行为）。
      expect(detailGetCalls(fetchMock)).toBe(2)

      unmount()

      await act(async () => {
        vi.advanceTimersByTime(5000)
      })
      // unmount 后轮询停止：detail GET 不再增加。
      expect(detailGetCalls(fetchMock)).toBe(2)
    }
  )

  it('reloads questions.json when its producer node completes', async () => {
    let detailRequests = 0
    let artifactRequests = 0
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/jobs/j1') {
        detailRequests += 1
        const completed = detailRequests > 1
        return Promise.resolve({
          ok: true,
          json: async () => ({
            ...mockDetail,
            job: {
              ...mockDetail.job,
              source_type: 'question',
              status: completed ? 'completed' : 'running',
            },
            nodes: [
              {
                ...mockDetail.nodes[0],
                node_key: 'fetch_items',
                capability: 'fetch_items',
                outputs: ['questions.json'],
                status: completed ? 'completed' : 'running',
                finished_at: completed ? '2026-06-18T10:00:00Z' : undefined,
              },
            ],
          }),
        })
      }
      if (url === '/api/jobs/j1/artifacts/questions.json') {
        artifactRequests += 1
        if (artifactRequests === 1) {
          return Promise.resolve({
            ok: false,
            status: 404,
            text: async () => JSON.stringify({ detail: 'Artifact not found' }),
          })
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({
            content: JSON.stringify({
              questions: [
                {
                  question_id: 'Q100',
                  normalized: { stem: '<p>Generated later</p>' },
                },
              ],
            }),
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    expect(await screen.findByText('Artifact not found')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })

    expect(await screen.findByText('Generated later')).toBeInTheDocument()
    expect(artifactRequests).toBe(2)
  })

  it('does not poll detail when job is completed', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'completed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(detailGetCalls(fetchMock)).toBe(1)

    await act(async () => {
      vi.advanceTimersByTime(5000)
    })
    // No detail poll for a completed job；mount 时的 workers invalidate
    // 在无活跃观察者（本页未挂 Worker 状态列表）时不产生请求。
    expect(detailGetCalls(fetchMock)).toBe(1)
  })

  it('disables rerun and package for a running job', async () => {
    vi.stubGlobal('fetch', createFetchMock())

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(screen.getByLabelText('重跑')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('打包')).toHaveAttribute('disabled')
    expect(screen.getByLabelText('删除')).not.toHaveAttribute('disabled')
  })

  it('reruns a selected node and refreshes detail', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'failed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('重跑').click()
    })

    expect(screen.getByText('选择重跑节点')).toBeInTheDocument()

    await act(async () => {
      screen.getByText('确认重跑').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/nodes/extract/rerun',
        expect.objectContaining({ method: 'POST' })
      )
    })
  })

  it('packages a completed job and opens download URL', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const fetchMock = createFetchMock({
      detailStatus: 'completed',
      packageUrl: '/api/workspaces/ws1/packages/j1.zip',
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('打包').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/workspaces/ws1/jobs/package',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ job_ids: ['j1'] }),
        })
      )
    })
    expect(openSpy).toHaveBeenCalledWith(
      '/api/workspaces/ws1/packages/j1.zip',
      '_blank'
    )
    openSpy.mockRestore()
  })

  it('deletes the job after confirm and navigates back to the workspace main page', async () => {
    const fetchMock = createFetchMock()
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('删除').click()
    })

    expect(screen.getByText(/确定删除任务/)).toBeInTheDocument()

    await act(async () => {
      screen.getByText('删除').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1',
        expect.objectContaining({ method: 'DELETE' })
      )
    })
    expect(screen.getByTestId('workspace-main-page')).toBeInTheDocument()
  })

  it('runs to a selected target and refreshes detail', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'failed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('运行到').click()
    })

    expect(screen.getByText('选择运行到节点')).toBeInTheDocument()

    await act(async () => {
      const chip = screen.getByTestId('target-chip-review')
      fireEvent.click(chip)
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/run-to',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ target_node_key: 'review' }),
        })
      )
    })
  })

  it('runs to a target from a selected start node', async () => {
    const fetchMock = createFetchMock({ detailStatus: 'failed' })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('运行到').click()
    })

    await act(async () => {
      const chip = screen.getByTestId('target-chip-review')
      fireEvent.click(chip)
    })

    await act(async () => {
      const chip = screen.getByTestId('start-chip-generate')
      fireEvent.click(chip)
    })

    await act(async () => {
      screen.getByText('确认运行到').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/run-to',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            target_node_key: 'review',
            start_node_key: 'generate',
          }),
        })
      )
    })
  })

  it('shows continue full flow when paused with target_reached reason', async () => {
    const fetchMock = createFetchMock({
      detailStatus: 'paused',
      pauseReason: 'target_reached',
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await waitFor(() => {
      expect(screen.getByLabelText('继续完整流程')).toBeInTheDocument()
    })

    await act(async () => {
      screen.getByLabelText('继续完整流程').click()
    })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/jobs/j1/continue',
        expect.objectContaining({ method: 'POST' })
      )
    })
  })

  it('renders actions as icon buttons in app bar, not text buttons in body', async () => {
    vi.stubGlobal('fetch', createFetchMock({ detailStatus: 'completed' }))

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    expect(screen.getByLabelText('重跑')).toBeInTheDocument()
    expect(screen.getByLabelText('打包')).toBeInTheDocument()
    // The old body action bar used text buttons with labels;
    // app bar actions are now icon buttons with aria-label.
    expect(
      screen.queryByText('重跑', { selector: 'button' })
    ).not.toBeInTheDocument()
  })

  it('opens artifact list after opening and closing fullscreen DAG dialog', async () => {
    vi.stubGlobal('fetch', createFetchMock({ detailStatus: 'completed' }))

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('提取')).toBeInTheDocument()
    })

    // Open fullscreen DAG dialog
    await act(async () => {
      screen.getByLabelText('查看 DAG').click()
    })
    expect(await screen.findByLabelText('关闭')).toBeInTheDocument()

    // Close fullscreen DAG dialog
    await act(async () => {
      screen.getByLabelText('关闭').click()
    })
    await waitFor(() => {
      expect(screen.queryByLabelText('关闭')).not.toBeInTheDocument()
    })

    // Click artifact folder button in app-bar actions
    await act(async () => {
      screen.getByLabelText('产物文件').click()
    })

    // Artifact list dialog should open
    expect(
      screen.getByRole('heading', { name: '产物文件' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'question.json' })
    ).toBeInTheDocument()
  })

  it('renders QuestionContentPanel for question jobs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url === '/api/jobs/j1') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              ...mockDetail,
              job: { ...mockDetail.job, source_type: 'question' },
            }),
          })
        }
        if (url === '/api/jobs/j1/artifacts/questions.json') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              content: JSON.stringify({
                questions: [
                  {
                    question_id: 'Q1',
                    normalized: { stem: '<p>Question stem</p>' },
                  },
                ],
              }),
            }),
          })
        }
        if (url === '/api/jobs/j1/artifacts/comprehension_info.json') {
          return Promise.reject(new Error('not found'))
        }
        return Promise.resolve({ ok: true, json: async () => ({}) })
      })
    )

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('Question stem')).toBeInTheDocument()
    })
    // issue #11：question 任务的结构化面板在上，通用产物预览在下。
    expect(screen.getByTestId('artifact-preview-panel')).toBeInTheDocument()
    expect(screen.getByText('question.json')).toBeInTheDocument()
  })

  it('renders generic artifact preview for video jobs (issue #11)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (url === '/api/jobs/j1') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              ...mockDetail,
              job: { ...mockDetail.job, source_type: 'video' },
            }),
          })
        }
        return Promise.resolve({ ok: true, json: async () => ({}) })
      })
    )

    renderPage()
    // 未知 source_type 不再白屏：通用产物预览兜底（video 空态 stub 已删）。
    await waitFor(() => {
      expect(screen.getByTestId('artifact-preview-panel')).toBeInTheDocument()
    })
    expect(screen.getByText('question.json')).toBeInTheDocument()
  })

  it('renders job token usage dialog when open', async () => {
    useUiStore.setState({ tokenUsageDialogOpen: true })
    vi.stubGlobal('fetch', createFetchMock())

    renderPage()

    expect(await screen.findByText('Job Token 使用分析')).toBeInTheDocument()
  })

  it('shows an error hint when the job id is missing', async () => {
    vi.stubGlobal('fetch', createFetchMock())

    render(
      <MemoryRouter initialEntries={['/workspaces/ws1/jobs']}>
        <Routes>
          <Route
            path="/workspaces/:workspaceId/jobs"
            element={<JobDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    )

    expect(await screen.findByText('缺少任务 ID')).toBeInTheDocument()
  })

  it('previews an artifact from the list and closes both dialogs', async () => {
    const base = createFetchMock({ detailStatus: 'completed' })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url === '/api/jobs/j1/artifacts/question.json') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              name: 'question.json',
              content: 'artifact-body',
            }),
          })
        }
        return base(url, init)
      })
    )

    renderPage()
    await screen.findByText('提取')

    // Open the artifact list, then close it without selecting.
    await act(async () => {
      screen.getByLabelText('产物文件').click()
    })
    expect(
      await screen.findByRole('heading', { name: '产物文件' })
    ).toBeInTheDocument()
    await act(async () => {
      screen.getByRole('button', { name: '关闭' }).click()
    })
    await waitFor(() => {
      expect(
        screen.queryByRole('heading', { name: '产物文件' })
      ).not.toBeInTheDocument()
    })

    // Reopen, select the artifact, and the preview shows its content.
    await act(async () => {
      screen.getByLabelText('产物文件').click()
    })
    await act(async () => {
      screen.getByRole('button', { name: 'question.json' }).click()
    })
    // issue #11 后 artifact 内容同时出现在左栏通用卡片与弹窗，按存在性断言。
    expect((await screen.findAllByText('artifact-body')).length).toBeGreaterThan(0)

    await act(async () => {
      screen.getByRole('button', { name: '关闭' }).click()
    })
    // 弹窗关闭；左栏通用卡片的同内容预览保留（issue #11 新行为）。
    await waitFor(() => {
      expect(
        screen.queryByRole('heading', { name: '产物文件' })
      ).not.toBeInTheDocument()
    })
    expect(screen.getAllByText('artifact-body').length).toBeGreaterThan(0)
  })

  it('shows the fetch error inside the artifact preview', async () => {
    const base = createFetchMock({ detailStatus: 'completed' })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url === '/api/jobs/j1/artifacts/question.json') {
          return Promise.resolve({
            ok: false,
            status: 500,
            text: async () => 'server boom',
            json: async () => ({}),
          })
        }
        return base(url, init)
      })
    )

    renderPage()
    await screen.findByText('提取')
    await act(async () => {
      screen.getByLabelText('产物文件').click()
    })
    await act(async () => {
      screen.getByRole('button', { name: 'question.json' }).click()
    })

    // issue #11 后错误同时呈现在左栏卡片与弹窗，按存在性断言。
    expect((await screen.findAllByText(/HTTP 500: server boom/)).length).toBeGreaterThan(0)
  })
})
