import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceRecord } from '../types'
import { useWorkspaceStore } from '../stores/workspaceStore'
import WorkspaceResources from './WorkspaceResources'

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

describe('WorkspaceResources', () => {
  beforeEach(() => {
    useWorkspaceStore.setState({
      workspaces: [],
      currentWorkspace: null,
      workspaceStats: {},
      loading: false,
      error: null,
    })
  })

  it('saves resource bindings for the current workspace', async () => {
    const workspace: WorkspaceRecord = {
      id: 'math',
      name: 'Math',
      default_pipeline_key: 'question_content',
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
      })
    })
    expect(await screen.findByText('配置已保存')).toBeInTheDocument()
    expect(screen.queryByLabelText('题目列表接口')).not.toBeInTheDocument()
  })

  it('does not show cms config for Video Hive system workspace', () => {
    render(<WorkspaceResources isVideoHive={true} />)

    expect(screen.getByText('Video Hive 资源配置由旧流程管理。')).toBeInTheDocument()
  })
})
