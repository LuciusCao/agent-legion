import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkspaceJobList from './WorkspaceJobList'
import { useWorkspaceStore } from '../stores/workspaceStore'

const navigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => navigate }
})

function createFetchMock(responses: Record<string, unknown>) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method || 'GET'} ${url}`
    const response = responses[key]
    if (response) {
      return Promise.resolve({ ok: true, json: async () => response })
    }
    return Promise.resolve({ ok: false, status: 404, text: async () => 'Not Found' })
  })
}

describe('WorkspaceJobList', () => {
  beforeEach(() => {
    navigate.mockReset()
    useWorkspaceStore.setState({
      currentWorkspace: { id: 'math_ws', name: '数学工作空间', default_pipeline_key: 'question_content' },
    })
  })

  it('creates jobs from selected knowledge code intake', async () => {
    const responses: Record<string, unknown> = {
      'GET /api/pipelines/question_content': {
        pipeline: {
          key: 'question_content',
          label: '题目内容生成',
          concurrency: { local: 8, agent: 2 },
          intake: {
            modes: [
              { key: 'question_ids', label: '题目 ID', resolver: 'direct.question_ids', task_entity: 'question', input_field: 'question_ids', resource: '' },
              { key: 'knowledge_codes', label: '知识点 Code', resolver: 'cms.questions_by_knowledge', task_entity: 'question', input_field: 'knowledge_codes', resource: 'questions_by_knowledge' },
            ],
          },
          nodes: [],
        },
      },
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
      'POST /api/workspaces/math_ws/job-batches': {
        batch: { id: 'b1' },
        created_count: 2,
        jobs: [
          { id: 'j1', workspace_id: 'math_ws', pipeline_key: 'question_content', source_id: 'q1', title: 'Q1', status: 'queued' },
          { id: 'j2', workspace_id: 'math_ws', pipeline_key: 'question_content', source_id: 'q2', title: 'Q2', status: 'queued' },
        ],
      },
    }

    // Override the GET jobs response after batch creation to return the created jobs
    const fetchMock = createFetchMock(responses)
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      const key = `${init?.method || 'GET'} ${url}`
      if (key === 'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            jobs: [
              { id: 'j1', workspace_id: 'math_ws', pipeline_key: 'question_content', source_id: 'q1', title: 'Q1', status: 'queued' },
              { id: 'j2', workspace_id: 'math_ws', pipeline_key: 'question_content', source_id: 'q2', title: 'Q2', status: 'queued' },
            ],
          }),
        })
      }
      const response = responses[key]
      if (response) {
        return Promise.resolve({ ok: true, json: async () => response })
      }
      return Promise.resolve({ ok: false, status: 404, text: async () => 'Not Found' })
    })

    vi.stubGlobal('fetch', fetchMock)

    const { container } = render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成')

    const chip = container.querySelector('md-filter-chip[label="知识点 Code"]')
    expect(chip).toBeInTheDocument()
    await act(async () => {
      ;(chip as HTMLElement).click()
    })

    const input = screen.getByLabelText('知识点 Code')
    await act(async () => {
      ;(input as HTMLInputElement).value = 'hx_cz_sy_qt_yq01'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    fireEvent.click(screen.getByText('创建任务'))

    await waitFor(() => expect(screen.getByText('已创建 2 个题目任务')).toBeInTheDocument())
    const batchCall = fetchMock.mock.calls.find(
      (call) => call[0] === '/api/workspaces/math_ws/job-batches'
    )
    expect(batchCall).toBeDefined()
    const body = JSON.parse(batchCall![1].body as string)
    expect(body).toMatchObject({
      pipeline_key: 'question_content',
      source_kind: 'knowledge_codes',
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
          { id: 'j1', workspace_id: 'math_ws', pipeline_key: 'question_content', source_id: 'q1', title: 'Q1', status: 'completed' },
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
      'GET /api/pipelines/question_content': {
        pipeline: {
          key: 'question_content',
          label: '题目内容生成',
          concurrency: { local: 8, agent: 2 },
          intake: {
            modes: [
              { key: 'question_ids', label: '题目 ID', resolver: 'direct.question_ids', task_entity: 'question', input_field: 'question_ids', resource: '' },
            ],
          },
          nodes: [],
        },
      },
      'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
        jobs: [],
      },
    })

    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      const key = `${init?.method || 'GET'} ${url}`
      if (key === 'POST /api/workspaces/math_ws/job-batches') {
        return Promise.resolve({ ok: false, status: 500, text: async () => 'Internal Server Error' })
      }
      const response: Record<string, unknown> = {
        'GET /api/pipelines/question_content': {
          pipeline: {
            key: 'question_content',
            label: '题目内容生成',
            concurrency: { local: 8, agent: 2 },
            intake: {
              modes: [
                { key: 'question_ids', label: '题目 ID', resolver: 'direct.question_ids', task_entity: 'question', input_field: 'question_ids', resource: '' },
              ],
            },
            nodes: [],
          },
        },
        'GET /api/workspaces/math_ws/jobs?pipeline_key=question_content': {
          jobs: [],
        },
      }
      const res = response[key]
      if (res) {
        return Promise.resolve({ ok: true, json: async () => res })
      }
      return Promise.resolve({ ok: false, status: 404, text: async () => 'Not Found' })
    })

    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <WorkspaceJobList isVideoHive={false} />
      </MemoryRouter>
    )

    await screen.findByText('题目内容生成')

    const input = screen.getByLabelText('题目 ID')
    await act(async () => {
      ;(input as HTMLInputElement).value = 'q1'
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    })

    fireEvent.click(screen.getByText('创建任务'))

    await waitFor(() => expect(screen.getByText('HTTP 500: Internal Server Error')).toBeInTheDocument())
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
              { key: 'question_ids', label: '题目 ID', resolver: 'direct.question_ids', task_entity: 'question', input_field: 'question_ids', resource: '' },
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

    await screen.findByText('题目内容生成')

    fireEvent.click(screen.getByText('创建任务'))

    await waitFor(() => expect(screen.getByText('请输入至少一个值')).toBeInTheDocument())
  })
})
