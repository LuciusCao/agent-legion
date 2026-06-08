import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspaceJobList from './WorkspaceJobList'
import { useWorkspaceStore } from '../stores/workspaceStore'

const navigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate }
})

function createFetchMock(responses: Record<string, unknown>) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method || 'GET'} ${url}`
    const response = responses[key]
    if (response) {
      return Promise.resolve({ ok: true, json: async () => response })
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      text: async () => 'Not Found',
    })
  })
}

const defaultWorkspace = {
  id: 'math_ws',
  name: '数学工作空间',
  default_pipeline_key: 'question_content',
  default_entity: 'question',
}

const pipelineResponse = {
  pipeline: {
    key: 'question_content',
    label: '题目内容生成',
    concurrency: { local: 8, agent: 2 },
    intake: {
      modes: [
        {
          key: 'direct_ids',
          label: '直接输入 ID',
          input_field: 'question_ids',
          resource: '',
        },
        {
          key: 'by_knowledge',
          label: '按知识点查询',
          input_field: 'knowledge_codes',
          resource: 'by_knowledge',
        },
      ],
    },
    nodes: [],
  },
}

describe('WorkspaceJobList', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    navigate.mockReset()
    useWorkspaceStore.setState({
      currentWorkspace: defaultWorkspace,
    })
  })

  it('creates jobs from selected knowledge code intake', async () => {
    const fetchMock = createFetchMock({
      'GET /api/pipelines/question_content': pipelineResponse,
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
      'POST /api/workspaces/math_ws/job-batches': {
        batch: { id: 'b1' },
        created_count: 2,
        jobs: [
          {
            id: 'j1',
            workspace_id: 'math_ws',
            pipeline_key: 'question_content',
            source_id: 'q1',
            title: 'Q1',
            status: 'queued',
          },
          {
            id: 'j2',
            workspace_id: 'math_ws',
            pipeline_key: 'question_content',
            source_id: 'q2',
            title: 'Q2',
            status: 'queued',
          },
        ],
      },
    })

    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成 · question')

    const chip = container.querySelector('md-filter-chip[label="按知识点查询"]')
    expect(chip).toBeInTheDocument()
    await act(async () => {
      ;(chip as HTMLElement).click()
    })

    const input = screen.getByLabelText('按知识点查询')
    await act(async () => {
      ;(input as HTMLInputElement).value = 'hx_cz_sy_qt_yq01'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    fireEvent.click(screen.getByText('创建任务'))

    await waitFor(() =>
      expect(screen.getByText('已创建 2 个题目任务')).toBeInTheDocument()
    )
    const batchCall = fetchMock.mock.calls.find(
      (call) => call[0] === '/api/workspaces/math_ws/job-batches'
    )
    expect(batchCall).toBeDefined()
    const body = JSON.parse(batchCall![1].body as string)
    expect(body).toMatchObject({
      pipeline_key: 'question_content',
      entity: 'question',
      source_kind: 'by_knowledge',
      question_ids: [],
      knowledge_codes: ['hx_cz_sy_qt_yq01'],
    })
  })

  it('opens a job detail route from the list', async () => {
    const fetchMock = createFetchMock({
      'GET /api/pipelines/question_content': {
        pipeline: {
          key: 'question_content',
          label: '题目内容生成',
          concurrency: { local: 8, agent: 2 },
          intake: { modes: [] },
          nodes: [],
        },
      },
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [
          {
            id: 'j1',
            workspace_id: 'math_ws',
            pipeline_key: 'question_content',
            source_id: 'q1',
            title: 'Q1',
            status: 'completed',
          },
        ],
      },
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    fireEvent.click(await screen.findByText('Q1'))
    expect(navigate).toHaveBeenCalledWith('/workspaces/math_ws/jobs/j1')
  })

  it('shows error when batch creation fails', async () => {
    const fetchMock = createFetchMock({
      'GET /api/pipelines/question_content': pipelineResponse,
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
    })

    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      const key = `${init?.method || 'GET'} ${url}`
      if (key === 'POST /api/workspaces/math_ws/job-batches') {
        return Promise.resolve({
          ok: false,
          status: 500,
          text: async () => 'Internal Server Error',
        })
      }
      const response: Record<string, unknown> = {
        'GET /api/pipelines/question_content': pipelineResponse,
        'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
          jobs: [],
        },
      }
      const res = response[key]
      if (res) {
        return Promise.resolve({ ok: true, json: async () => res })
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Not Found',
      })
    })

    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成 · question')

    const input = screen.getByLabelText('直接输入 ID')
    await act(async () => {
      ;(input as HTMLInputElement).value = 'q1'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    fireEvent.click(screen.getByText('创建任务'))

    await waitFor(() =>
      expect(
        screen.getByText('HTTP 500: Internal Server Error')
      ).toBeInTheDocument()
    )
  })

  it('shows validation error when input is empty', async () => {
    const fetchMock = createFetchMock({
      'GET /api/pipelines/question_content': {
        pipeline: {
          key: 'question_content',
          label: '题目内容生成',
          concurrency: { local: 8, agent: 2 },
          intake: {
            modes: [
              {
                key: 'direct_ids',
                label: '直接输入 ID',
                input_field: 'question_ids',
                resource: '',
              },
            ],
          },
          nodes: [],
        },
      },
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成 · question')

    fireEvent.click(screen.getByText('创建任务'))

    await waitFor(() =>
      expect(screen.getByText('请输入至少一个值')).toBeInTheDocument()
    )
  })

  it('hides disabled intake modes', async () => {
    useWorkspaceStore.setState({
      currentWorkspace: {
        ...defaultWorkspace,
        intake_config: { enabled_modes: ['direct_ids'] },
      },
    })

    const fetchMock = createFetchMock({
      'GET /api/pipelines/question_content': pipelineResponse,
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
    })
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成 · question')

    expect(
      container.querySelector('md-filter-chip[label="直接输入 ID"]')
    ).toBeInTheDocument()
    expect(
      container.querySelector('md-filter-chip[label="按知识点查询"]')
    ).not.toBeInTheDocument()
  })

  it('shows label override', async () => {
    useWorkspaceStore.setState({
      currentWorkspace: {
        ...defaultWorkspace,
        intake_config: { label_overrides: { direct_ids: '自定义名称' } },
      },
    })

    const fetchMock = createFetchMock({
      'GET /api/pipelines/question_content': pipelineResponse,
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
    })
    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成 · question')

    expect(
      container.querySelector('md-filter-chip[label="自定义名称"]')
    ).toBeInTheDocument()
  })

  it('sends entity in batch request', async () => {
    useWorkspaceStore.setState({
      currentWorkspace: {
        ...defaultWorkspace,
        default_entity: 'video',
      },
    })

    const fetchMock = createFetchMock({
      'GET /api/pipelines/question_content': pipelineResponse,
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
      'POST /api/workspaces/math_ws/job-batches': {
        batch: { id: 'b1' },
        created_count: 1,
        jobs: [
          {
            id: 'j1',
            workspace_id: 'math_ws',
            pipeline_key: 'question_content',
            source_id: 'v1',
            title: 'V1',
            status: 'queued',
          },
        ],
      },
    })

    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成 · video')

    const input = screen.getByLabelText('直接输入 ID')
    await act(async () => {
      ;(input as HTMLInputElement).value = 'v1'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    fireEvent.click(screen.getByText('创建任务'))

    await waitFor(() =>
      expect(screen.getByText('已创建 1 个题目任务')).toBeInTheDocument()
    )
    const batchCall = fetchMock.mock.calls.find(
      (call) => call[0] === '/api/workspaces/math_ws/job-batches'
    )
    expect(batchCall).toBeDefined()
    const body = JSON.parse(batchCall![1].body as string)
    expect(body).toMatchObject({
      pipeline_key: 'question_content',
      entity: 'video',
      source_kind: 'direct_ids',
      question_ids: ['v1'],
      knowledge_codes: [],
    })
  })
})
