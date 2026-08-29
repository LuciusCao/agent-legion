import { fireEvent, render, screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { postNodePromptPreview } from '../../../api/nodePromptPreview'
import type { NodePromptPreviewResponse } from '../../../api/nodePromptPreview'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import type { WorkflowNodeRecord } from '../../../types'
import { NodeDetailPreviewContext } from './nodeDetailPreviewContext'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft.parse'
import { WorkflowPromptPreviewPanel } from './WorkflowPromptPreviewPanel'

vi.mock('../../../api/nodePromptPreview', () => ({
  postNodePromptPreview: vi.fn(),
}))

const mockPreview = vi.mocked(postNodePromptPreview)

const node: WorkflowNodeRecord = {
  key: 'n1',
  label: '节点一',
  capability: 'cap',
  after: [],
  inputs: [],
  outputs: [],
}

const baseYaml = 'nodes:\n  n1:\n    capability: cap\n'

function previewResponse(
  overrides?: Partial<NodePromptPreviewResponse>
): NodePromptPreviewResponse {
  return {
    effective_prompt: 'ENVELOPE\n默认指令文本\n',
    default_instructions: '默认指令文本',
    custom_instructions: '',
    is_default: true,
    skill_key: 'demo/review',
    ...overrides,
  }
}

function renderPanel(options?: {
  definitionYaml?: string
  setDefinitionYaml?: (value: string) => void
  readOnly?: boolean
  fallbackSkillKey?: string
  showPreview?: (kind: 'prompt' | 'skill') => void
}) {
  return render(
    <MemoryRouter initialEntries={['/workspaces/ws-1/studio']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/studio"
          element={
            <NodeDetailPreviewContext.Provider
              value={options?.showPreview ?? (() => {})}
            >
              <WorkflowPromptPreviewPanel
                node={node}
                fallbackSkillKey={options?.fallbackSkillKey ?? ''}
                definitionYaml={options?.definitionYaml ?? baseYaml}
                setDefinitionYaml={options?.setDefinitionYaml ?? (() => {})}
                readOnly={options?.readOnly}
              />
            </NodeDetailPreviewContext.Provider>
          }
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('WorkflowPromptPreviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPreview.mockResolvedValue(previewResponse())
  })

  it('shows the assembled default instructions and the full effective prompt', async () => {
    renderPanel()

    // 节点指令留空：编辑区展示后端组装的默认指令并标注默认。
    expect(await screen.findByDisplayValue('默认指令文本')).toBeInTheDocument()
    expect(screen.getByText('默认（按节点信息自动组装）')).toBeInTheDocument()
    // 完整运行 Prompt（含平台信封）只读展示。
    expect(
      screen.getByText('完整运行 Prompt（含平台信封）')
    ).toBeInTheDocument()
    expect(screen.getByText(/ENVELOPE/)).toBeInTheDocument()
    expect(mockPreview).toHaveBeenCalledWith('ws-1', 'n1', baseYaml)
    // 默认态不提供重置按钮。
    expect(
      screen.queryByRole('button', { name: '重置为默认' })
    ).not.toBeInTheDocument()
  })

  it('writes edits into the draft YAML via the execution.prompt patch', async () => {
    const setDefinitionYaml = vi.fn()
    renderPanel({ setDefinitionYaml })

    fireEvent.change(await screen.findByLabelText('节点指令'), {
      target: { value: '自定义指令' },
    })

    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.execution?.prompt).toBe(
      '自定义指令'
    )
  })

  it('resets a custom prompt back to the default assembly', async () => {
    const yamlWithPrompt = [
      'nodes:',
      '  n1:',
      '    capability: cap',
      '    execution:',
      '      prompt: 旧的自定义',
      '',
    ].join('\n')
    mockPreview.mockResolvedValue(
      previewResponse({
        is_default: false,
        custom_instructions: '旧的自定义',
      })
    )
    const setDefinitionYaml = vi.fn()
    renderPanel({ definitionYaml: yamlWithPrompt, setDefinitionYaml })

    // 自定义态：编辑区显示草稿里的自定义内容，不标注默认。
    expect(await screen.findByDisplayValue('旧的自定义')).toBeInTheDocument()
    expect(
      screen.queryByText('默认（按节点信息自动组装）')
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重置为默认' }))
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    // 清空 prompt 键（execution 块随空键一并清除）。
    expect(parseWorkflowNode(nextYaml, 'n1')?.execution?.prompt).toBeUndefined()
  })

  it('jumps to the skill preview via the bound skill chip', async () => {
    const showPreview = vi.fn()
    renderPanel({ showPreview })

    fireEvent.click(await screen.findByRole('button', { name: 'demo/review' }))
    expect(showPreview).toHaveBeenCalledWith('skill')
  })

  it('shows an unbound chip when neither the preview nor the catalog binds a skill', async () => {
    mockPreview.mockResolvedValue(previewResponse({ skill_key: null }))
    renderPanel()

    expect(await screen.findByText('未绑定技能')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'demo/review' })
    ).not.toBeInTheDocument()
  })

  it('disables editing in readOnly mode', async () => {
    const yamlWithPrompt = [
      'nodes:',
      '  n1:',
      '    capability: cap',
      '    execution:',
      '      prompt: 自定义',
      '',
    ].join('\n')
    renderPanel({ definitionYaml: yamlWithPrompt, readOnly: true })

    expect(await screen.findByLabelText('节点指令')).toBeDisabled()
    expect(
      screen.queryByRole('button', { name: '重置为默认' })
    ).not.toBeInTheDocument()
  })

  it('collapses and re-expands the full prompt preview', async () => {
    renderPanel()
    expect(await screen.findByText(/ENVELOPE/)).toBeInTheDocument()

    fireEvent.click(
      screen.getByRole('button', { name: '完整运行 Prompt（含平台信封）' })
    )
    expect(screen.queryByText(/ENVELOPE/)).not.toBeInTheDocument()
    fireEvent.click(
      screen.getByRole('button', { name: '完整运行 Prompt（含平台信封）' })
    )
    expect(screen.getByText(/ENVELOPE/)).toBeInTheDocument()
  })
})
