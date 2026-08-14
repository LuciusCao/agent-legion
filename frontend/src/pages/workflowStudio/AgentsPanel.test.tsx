import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  archiveAgent,
  createAgentDefinition,
  fetchAgentDefinition,
  fetchAgentDefinitions,
  fetchAgentVersions,
  rollbackAgent,
  validateSkillPath,
} from '../../api'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import {
  createTestQueryClient,
  TestQueryProvider,
} from '../../testing/testQueryClient'
import type { AgentListItem, AgentVersion } from '../../types'
import { AgentsPanel } from './AgentsPanel'

vi.mock('../../api', () => ({
  fetchAgentDefinitions: vi.fn(),
  fetchAgentDefinition: vi.fn(),
  createAgentDefinition: vi.fn(),
  saveAgentDraft: vi.fn(),
  publishAgent: vi.fn(),
  archiveAgent: vi.fn(),
  copyAgent: vi.fn(),
  fetchAgentVersions: vi.fn(),
  rollbackAgent: vi.fn(),
  validateSkillPath: vi.fn(),
}))

const mockList = vi.mocked(fetchAgentDefinitions)
const mockDetail = vi.mocked(fetchAgentDefinition)
const mockCreate = vi.mocked(createAgentDefinition)
const mockArchive = vi.mocked(archiveAgent)
const mockVersions = vi.mocked(fetchAgentVersions)
const mockRollback = vi.mocked(rollbackAgent)
const mockValidate = vi.mocked(validateSkillPath)

const agent: AgentListItem = {
  agent_id: 'key-info-v1',
  capability: 'generate_key_info',
  runtime: 'pi',
  skill: 'ns/skill',
  version: 2,
  status: 'published',
  has_draft: false,
  published_at: '2026-08-01T00:00:00Z',
}

const publishedVersion: AgentVersion = {
  id: 'ver-2',
  agent_id: 'key-info-v1',
  version: 2,
  status: 'published',
  definition: {
    capability: 'generate_key_info',
    runtime: 'pi',
    skill: 'ns/skill',
    tools: ['read'],
    config_schema: { type: 'object' },
  },
  definition_hash: 'deadbeef',
  created_by: 'admin',
  created_at: '2026-08-01T00:00:00Z',
  published_at: '2026-08-01T01:00:00Z',
}

function renderPanel(initialSelectedId: string | null = null) {
  return render(
    <TestQueryProvider>
      <AgentsPanel initialSelectedId={initialSelectedId} />
    </TestQueryProvider>
  )
}

describe('AgentsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue({ agents: [agent] })
    mockDetail.mockResolvedValue({
      agent_id: 'key-info-v1',
      latest: publishedVersion,
      published: publishedVersion,
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  it('lists agents with capability, runtime and status', async () => {
    renderPanel()

    expect(
      await screen.findByRole('button', { name: /key-info-v1/ })
    ).toBeInTheDocument()
    expect(screen.getByText('generate_key_info')).toBeInTheDocument()
    expect(screen.getByText('pi')).toBeInTheDocument()
    expect(screen.getByText('已发布')).toBeInTheDocument()
  })

  it('loads the selected agent into the editor', async () => {
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: /key-info-v1/ }))

    await waitFor(() =>
      expect(screen.getByLabelText('Capability')).toHaveValue(
        'generate_key_info'
      )
    )
    expect(mockDetail).toHaveBeenCalledWith('key-info-v1')
    expect(screen.getByLabelText('Agent ID')).toHaveValue('key-info-v1')
    expect(screen.getByLabelText('Skill')).toHaveValue('ns/skill')
    // 无草稿时不可直接发布
    expect(screen.getByRole('button', { name: '发布' })).toBeDisabled()
  })

  it('opens the focused agent directly when initialSelectedId is given', async () => {
    renderPanel('key-info-v1')

    await waitFor(() =>
      expect(screen.getByLabelText('Agent ID')).toHaveValue('key-info-v1')
    )
    expect(mockDetail).toHaveBeenCalledWith('key-info-v1')
  })

  it('creates a new agent draft after skill validation', async () => {
    mockValidate.mockResolvedValue({
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: [],
      latest_tag: null,
      locked_ref: null,
    })
    mockCreate.mockResolvedValue(publishedVersion)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: '新建' }))
    fireEvent.change(screen.getByLabelText('Agent ID'), {
      target: { value: 'new-agent' },
    })
    fireEvent.change(screen.getByLabelText('Capability'), {
      target: { value: 'review_key_info' },
    })
    fireEvent.change(screen.getByLabelText('Skill 路径（绝对路径）'), {
      target: { value: '/abs/skill' },
    })
    fireEvent.click(screen.getByRole('button', { name: '校验' }))
    await waitFor(() =>
      expect(screen.getByLabelText('Skill')).toHaveValue('ns/skill')
    )

    fireEvent.click(screen.getByRole('button', { name: '创建草稿' }))

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          agent_id: 'new-agent',
          capability: 'review_key_info',
          runtime: 'pi',
          skill: 'ns/skill',
        })
      )
    )
  })

  it('archives an agent after confirmation', async () => {
    mockArchive.mockResolvedValue({ archived: 2 })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: /key-info-v1/ }))
    fireEvent.click(await screen.findByRole('button', { name: '归档' }))

    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith('key-info-v1'))
    expect(window.confirm).toHaveBeenCalled()
  })

  it('invalidates the studio executor catalog when agent definitions refresh', async () => {
    mockArchive.mockResolvedValue({ archived: 2 })
    const client = createTestQueryClient()
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')
    render(
      <QueryClientProvider client={client}>
        <AgentsPanel />
      </QueryClientProvider>
    )

    fireEvent.click(await screen.findByRole('button', { name: /key-info-v1/ }))
    fireEvent.click(await screen.findByRole('button', { name: '归档' }))

    await waitFor(() => expect(mockArchive).toHaveBeenCalledWith('key-info-v1'))
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: extraQueryKeys.studioExecutorCatalog(),
      })
    )
  })

  it('rolls back from the versions dialog', async () => {
    mockVersions.mockResolvedValue({
      versions: [
        {
          id: 'ver-1',
          agent_id: 'key-info-v1',
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

    fireEvent.click(await screen.findByRole('button', { name: /key-info-v1/ }))
    fireEvent.click(await screen.findByRole('button', { name: '版本历史' }))

    expect(await screen.findByText('v1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '回滚' }))

    await waitFor(() =>
      expect(mockRollback).toHaveBeenCalledWith('key-info-v1', 1)
    )
  })
})
