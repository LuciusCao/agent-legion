import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchExecutorDefinition,
  fetchExecutorDefinitions,
  fetchExecutorVersions,
  publishExecutor,
  rollbackExecutor,
  saveExecutorDraft,
} from '../../api'
import type { ExecutorListItem, ExecutorVersion } from '../../types'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { ExecutorsPanel } from './ExecutorsPanel'

vi.mock('../../api', () => ({
  fetchExecutorDefinitions: vi.fn(),
  fetchExecutorDefinition: vi.fn(),
  createExecutorDefinition: vi.fn(),
  saveExecutorDraft: vi.fn(),
  publishExecutor: vi.fn(),
  archiveExecutor: vi.fn(),
  copyExecutor: vi.fn(),
  fetchExecutorVersions: vi.fn(),
  rollbackExecutor: vi.fn(),
}))

const mockList = vi.mocked(fetchExecutorDefinitions)
const mockDetail = vi.mocked(fetchExecutorDefinition)
const mockSaveDraft = vi.mocked(saveExecutorDraft)
const mockPublish = vi.mocked(publishExecutor)
const mockVersions = vi.mocked(fetchExecutorVersions)
const mockRollback = vi.mocked(rollbackExecutor)

const executor: ExecutorListItem = {
  executor_id: 'code-default',
  kind: 'code',
  global_capacity: 16,
  capabilities: ['clean_and_parse', 'fetch_questions'],
  version: 1,
  status: 'published',
  has_draft: false,
  published_at: '2026-08-01T00:00:00Z',
}

const publishedVersion: ExecutorVersion = {
  id: 'ver-1',
  executor_id: 'code-default',
  version: 1,
  status: 'published',
  definition: {
    kind: 'code',
    global_capacity: 16,
    capabilities: {
      clean_and_parse: { path: 'workflow_nodes/question_clean_parse.py' },
    },
  },
  definition_hash: 'deadbeef',
  created_by: 'admin',
  created_at: '2026-08-01T00:00:00Z',
  published_at: '2026-08-01T01:00:00Z',
}

function renderPanel() {
  return render(
    <TestQueryProvider>
      <ExecutorsPanel />
    </TestQueryProvider>
  )
}

describe('ExecutorsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue({ executors: [executor] })
    mockDetail.mockResolvedValue({
      executor_id: 'code-default',
      latest: publishedVersion,
      published: publishedVersion,
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  it('lists executors with kind, capacity and capability count', async () => {
    renderPanel()

    expect(
      await screen.findByRole('button', { name: /code-default/ })
    ).toBeInTheDocument()
    expect(screen.getByText('code')).toBeInTheDocument()
    expect(screen.getByText('容量 16')).toBeInTheDocument()
    expect(screen.getByText('2 个 capability')).toBeInTheDocument()
    expect(screen.getByText('已发布')).toBeInTheDocument()
    // 热生效提示显著可见
    expect(screen.getByRole('note')).toHaveTextContent('热生效')
  })

  it('saves a draft via PUT with edited capacity', async () => {
    mockSaveDraft.mockResolvedValue({
      ...publishedVersion,
      version: 2,
      status: 'draft',
    })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: /code-default/ }))
    await waitFor(() =>
      expect(screen.getByLabelText('Global Capacity')).toHaveValue(16)
    )
    fireEvent.change(screen.getByLabelText('Global Capacity'), {
      target: { value: '4' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() =>
      expect(mockSaveDraft).toHaveBeenCalledWith(
        'code-default',
        expect.objectContaining({ kind: 'code', global_capacity: 4 })
      )
    )
  })

  it('publishes the saved draft via POST', async () => {
    mockDetail.mockResolvedValue({
      executor_id: 'code-default',
      latest: { ...publishedVersion, version: 2, status: 'draft' },
      published: publishedVersion,
    })
    mockPublish.mockResolvedValue({
      ...publishedVersion,
      version: 2,
      status: 'published',
    })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: /code-default/ }))
    fireEvent.click(await screen.findByRole('button', { name: '发布' }))

    await waitFor(() =>
      expect(mockPublish).toHaveBeenCalledWith('code-default')
    )
  })

  it('blocks saving when capabilities JSON is invalid', async () => {
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: /code-default/ }))
    const field = await screen.findByLabelText('capabilities（JSON，可空）')
    await waitFor(() =>
      expect((field as HTMLTextAreaElement).value).toContain('clean_and_parse')
    )
    fireEvent.change(field, { target: { value: '{not json' } })
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'capabilities 不是合法 JSON'
    )
    expect(mockSaveDraft).not.toHaveBeenCalled()
  })

  it('rolls back from the versions dialog', async () => {
    mockVersions.mockResolvedValue({
      versions: [
        {
          id: 'ver-0',
          executor_id: 'code-default',
          version: 1,
          status: 'published',
          definition_hash: 'beef',
          created_by: 'admin',
          created_at: '2026-07-01T00:00:00Z',
          published_at: '2026-07-01T01:00:00Z',
        },
      ],
    })
    mockRollback.mockResolvedValue(publishedVersion)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: /code-default/ }))
    fireEvent.click(await screen.findByRole('button', { name: '版本历史' }))

    expect(await screen.findByText('v1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '回滚' }))

    await waitFor(() =>
      expect(mockRollback).toHaveBeenCalledWith('code-default', 1)
    )
  })
})
