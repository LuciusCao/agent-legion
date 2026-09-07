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
    // #513：平台提示词面板只显示信封半区（不含节点指令尾巴）。
    platform_prompt: 'ENVELOPE',
    prompt_mode: 'append',
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

    // 自定义提示词留空：编辑区展示后端组装的默认指令并标注默认。
    expect(await screen.findByDisplayValue('默认指令文本')).toBeInTheDocument()
    // #513 复审：默认留空是常态，不挂「默认」徽标。
    expect(
      screen.queryByText('默认（按节点信息自动组装）')
    ).not.toBeInTheDocument()
    // 平台提示词（#513 前叫平台信封）只读展示，带不可修改说明。
    expect(screen.getByText('平台提示词')).toBeInTheDocument()
    expect(
      screen.getByText('根据 workflow 自动生成，不可修改')
    ).toBeInTheDocument()
    expect(screen.getByText(/ENVELOPE/)).toBeInTheDocument()
    expect(mockPreview).toHaveBeenCalledWith('ws-1', 'n1', baseYaml)
    // 默认态不提供重置按钮。
    expect(
      screen.queryByRole('button', { name: '清空' })
    ).not.toBeInTheDocument()
  })

  it('renders the platform prompt above the editor (#513 layout)', async () => {
    renderPanel()
    await screen.findByDisplayValue('默认指令文本')
    const platform = screen.getByRole('button', { name: '平台提示词' })
    const editor = screen.getByLabelText('自定义提示词')
    // #513：平台提示词置顶（先读不可修改部分），编辑区随后。
    expect(
      platform.compareDocumentPosition(editor) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it('writes edits into the draft YAML via the execution.prompt patch', async () => {
    const setDefinitionYaml = vi.fn()
    renderPanel({ setDefinitionYaml })

    fireEvent.change(await screen.findByLabelText('自定义提示词'), {
      target: { value: '自定义指令' },
    })

    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.execution?.prompt).toBe(
      '自定义指令'
    )
  })

  it('switches the prompt mode into the draft YAML (#513)', async () => {
    const setDefinitionYaml = vi.fn()
    renderPanel({ setDefinitionYaml })

    // 模式选择器默认追加；切到覆写写入 execution.prompt_mode。
    const modeField = await screen.findByLabelText('模式')
    fireEvent.mouseDown(modeField)
    // 打开后的选项面：追加选中（默认），覆写可选。
    const appendOption = await screen.findByRole('option', {
      name: /追加（平台提示词 \+ 自定义提示词）/,
    })
    expect(appendOption.getAttribute('aria-selected')).toBe('true')
    fireEvent.click(
      screen.getByRole('option', { name: /覆写（仅自定义提示词）/ })
    )
    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(parseWorkflowNode(nextYaml, 'n1')?.execution?.prompt_mode).toBe(
      'overwrite'
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

    // 自定义态：编辑区显示草稿里的自定义内容，挂「已自定义」徽标。
    expect(await screen.findByDisplayValue('旧的自定义')).toBeInTheDocument()
    expect(screen.getByText('已自定义')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '清空' }))
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

  it('treats a non-string prompt value as unset instead of crashing (codex P1 family)', async () => {
    // `prompt: 123`：合法 YAML 非法契约值——按未配置归一，编辑区回落
    // 默认指令，打开「查看 Prompt」不再渲染抛错（reviewer-m4 r2）。
    const yamlWithJunkPrompt = [
      'nodes:',
      '  n1:',
      '    capability: cap',
      '    execution:',
      '      prompt: 123',
      '',
    ].join('\n')
    renderPanel({ definitionYaml: yamlWithJunkPrompt })

    expect(await screen.findByDisplayValue('默认指令文本')).toBeInTheDocument()
    expect(screen.queryByText('已自定义')).not.toBeInTheDocument()
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

    expect(await screen.findByLabelText('自定义提示词')).toBeDisabled()
    expect(
      screen.queryByRole('button', { name: '清空' })
    ).not.toBeInTheDocument()
  })

  it('shows the failure alert without a lingering loading placeholder', async () => {
    mockPreview.mockRejectedValue(new Error('boom'))
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('预览加载失败')
    // 失败后不再停留「正在加载默认指令…」placeholder。
    expect(screen.getByLabelText('自定义提示词')).toHaveAttribute(
      'placeholder',
      ''
    )
  })

  it('collapses and re-expands the full prompt preview', async () => {
    renderPanel()
    expect(await screen.findByText(/ENVELOPE/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '平台提示词' }))
    expect(screen.queryByText(/ENVELOPE/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '平台提示词' }))
    expect(screen.getByText(/ENVELOPE/)).toBeInTheDocument()
  })
})
