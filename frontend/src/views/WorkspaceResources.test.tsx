import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PipelineResponse, WorkspaceRecord } from '../types'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { fetchPipelineDefinition } from '../api'
import WorkspaceResources from './WorkspaceResources'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchPipelineDefinition:
      vi.fn<(_key: string) => Promise<PipelineResponse>>(),
  }
})

const mockPipeline: PipelineResponse = {
  pipeline: {
    key: 'question_content',
    label: 'Question Content',
    concurrency: { local: 2, agent: 2 },
    intake: {
      modes: [
        {
          key: 'direct_ids',
          label: 'Direct IDs',
          input_field: 'question_ids',
          resource: 'question_detail',
        },
        {
          key: 'by_knowledge',
          label: 'By Knowledge',
          input_field: 'knowledge_codes',
          resource: 'by_knowledge',
        },
      ],
    },
    nodes: [],
  },
}

async function inputValue(label: string, value: string) {
  const field = screen.getByLabelText(label) as HTMLInputElement
  await act(async () => {
    field.value = value
    field.dispatchEvent(new InputEvent('input', { bubbles: true }))
  })
}

async function clickCheckbox(label: string) {
  const checkbox = screen.getByLabelText(label) as HTMLInputElement
  await act(async () => {
    checkbox.click()
  })
}

async function selectOption(label: string, value: string) {
  const select = screen.getByLabelText(label) as HTMLSelectElement
  await act(async () => {
    select.value = value
    select.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

describe('WorkspaceResources', () => {
  beforeEach(() => {
    useWorkspaceStore.setState({
      workspaces: [],
      currentWorkspace: null,
      workspaceStats: {},
      loading: false,
      error: null,
    })
    vi.mocked(fetchPipelineDefinition).mockReset()
    vi.mocked(fetchPipelineDefinition).mockResolvedValue(mockPipeline)
  })

  it('saves resource bindings for the current workspace', async () => {
    const workspace: WorkspaceRecord = {
      id: 'math',
      name: 'Math',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
      intake_config: {
        enabled_modes: ['direct_ids'],
        label_overrides: {},
      },
      resource_config: {
        resources: {
          questions_by_knowledge: {
            provider: 'cms.question.list_by_knowledge',
            config: {
              bank_version: 'v5',
            },
          },
        },
      },
    }
    const updateWorkspace = vi.fn().mockResolvedValue({
      ...workspace,
      resource_config: {
        resources: {
          questions_by_knowledge: {
            provider: 'cms.question.list_by_knowledge',
            config: { bank_version: 'v5' },
          },
          question_detail: {
            provider: 'cms.question.detail',
            config: { bank_version: 'v5', subject_id: '5' },
          },
        },
      },
    })
    useWorkspaceStore.setState({
      currentWorkspace: workspace,
      updateWorkspace,
    })

    render(<WorkspaceResources isVideoHive={false} />)
    await waitFor(() =>
      expect(fetchPipelineDefinition).toHaveBeenCalledWith('question_content')
    )

    await clickCheckbox('启用题目详情')
    await inputValue('知识点下题目列表 学科 ID', '5')
    await inputValue('题目详情 题库版本', 'v5-detail')
    await inputValue('题目详情 学科 ID', '2')
    fireEvent.click(screen.getByText('保存配置'))

    await waitFor(() => {
      expect(updateWorkspace).toHaveBeenCalledWith('math', {
        resource_config: {
          resources: {
            questions_by_knowledge: {
              provider: 'cms.question.list_by_knowledge',
              config: {
                bank_version: 'v5',
                country_id: '',
                subject_id: '5',
                page_size: '50',
              },
            },
            question_detail: {
              provider: 'cms.question.detail',
              config: {
                bank_version: 'v5-detail',
                country_id: '',
                subject_id: '2',
              },
            },
          },
        },
        default_entity: 'question',
        intake_config: {
          enabled_modes: ['direct_ids'],
          label_overrides: {},
        },
      })
    })
    expect(await screen.findByText('配置已保存')).toBeInTheDocument()
    expect(screen.queryByLabelText('题目列表接口')).not.toBeInTheDocument()
  })

  it('does not show cms config for Video Hive system workspace', () => {
    render(<WorkspaceResources isVideoHive={true} />)

    expect(
      screen.getByText('Video Hive 资源配置由旧流程管理。')
    ).toBeInTheDocument()
  })

  it('renders intake config with entity selector and mode checkboxes', async () => {
    const workspace: WorkspaceRecord = {
      id: 'math',
      name: 'Math',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
      intake_config: {
        enabled_modes: ['direct_ids'],
        label_overrides: { direct_ids: 'Custom Direct' },
      },
    }
    useWorkspaceStore.setState({
      currentWorkspace: workspace,
      updateWorkspace: vi.fn().mockResolvedValue(workspace),
    })

    render(<WorkspaceResources isVideoHive={false} />)
    await waitFor(() =>
      expect(fetchPipelineDefinition).toHaveBeenCalledWith('question_content')
    )

    expect(screen.getByText('Intake 配置')).toBeInTheDocument()
    expect(screen.getByLabelText('处理对象 (Entity)')).toBeInTheDocument()
    expect(screen.getByLabelText('Direct IDs')).toBeInTheDocument()
    expect(screen.getByLabelText('By Knowledge')).toBeInTheDocument()

    const directOverride = screen.getByLabelText('Direct IDs 显示名称')
    expect(directOverride).toHaveAttribute('value', 'Custom Direct')
  })

  it('saves intake config with updateWorkspace', async () => {
    const workspace: WorkspaceRecord = {
      id: 'math',
      name: 'Math',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
      intake_config: {
        enabled_modes: ['direct_ids'],
        label_overrides: {},
      },
    }
    const updateWorkspace = vi.fn().mockResolvedValue(workspace)
    useWorkspaceStore.setState({
      currentWorkspace: workspace,
      updateWorkspace,
    })

    render(<WorkspaceResources isVideoHive={false} />)
    await waitFor(() =>
      expect(fetchPipelineDefinition).toHaveBeenCalledWith('question_content')
    )

    await selectOption('处理对象 (Entity)', 'video')
    await clickCheckbox('By Knowledge')
    await inputValue('By Knowledge 显示名称', '按知识点')

    fireEvent.click(screen.getByText('保存配置'))

    await waitFor(() => {
      expect(updateWorkspace).toHaveBeenCalledWith(
        'math',
        expect.objectContaining({
          default_entity: 'video',
          intake_config: {
            enabled_modes: expect.arrayContaining([
              'direct_ids',
              'by_knowledge',
            ]),
            label_overrides: expect.objectContaining({
              by_knowledge: '按知识点',
            }),
          },
        })
      )
    })
  })

  it('validation prevents saving with no modes enabled', async () => {
    const workspace: WorkspaceRecord = {
      id: 'math',
      name: 'Math',
      default_pipeline_key: 'question_content',
      default_entity: 'question',
      intake_config: {
        enabled_modes: ['direct_ids'],
        label_overrides: {},
      },
    }
    const updateWorkspace = vi.fn().mockResolvedValue(workspace)
    useWorkspaceStore.setState({
      currentWorkspace: workspace,
      updateWorkspace,
    })

    render(<WorkspaceResources isVideoHive={false} />)
    await waitFor(() =>
      expect(fetchPipelineDefinition).toHaveBeenCalledWith('question_content')
    )

    await clickCheckbox('Direct IDs')
    fireEvent.click(screen.getByText('保存配置'))

    expect(
      await screen.findByText(/至少启用一种 intake mode/i)
    ).toBeInTheDocument()
    expect(updateWorkspace).not.toHaveBeenCalled()
  })
})
