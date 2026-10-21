import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import type { WorkflowNodeRecord } from '../../../types'
import {
  withStudioProviders,
  makeStudioView,
} from '../shared/testStudioProviders'
import { WorkflowNodeRuntimeChannelSection } from './WorkflowNodeRuntimeChannelSection'

// per-runtime 工具目录（tools chip 编辑器的可选项来源）。
vi.mock('./useAgentRuntimes', () => ({
  useRuntimeToolEntries: () => [
    { name: 'web_search', tier: 'default' },
    { name: 'file_read', tier: 'opt-in' },
  ],
}))

// datalist 选项来自在线 Worker 声明的 runtime/provider/model。
vi.mock('../shared/useWorkspaceRuntimeModels', () => ({
  useWorkspaceRuntimeModels: () => ({
    data: {
      runtimes: {
        pi: { deepseek: ['your-model-b', 'your-model-c'] },
      },
    },
  }),
}))

const node: WorkflowNodeRecord = {
  key: 'generate_key_info',
  label: '生成关键信息',
  capability: 'generate_key_info',
  node_type: 'agent',
  after: [],
  inputs: [],
  outputs: [],
  terminal: null,
}

const baselineYaml = `execution:\n  provider: deepseek\n  model: your-model-b\n  thinking: low\nnodes:\n  generate_key_info:\n    type: agent\n    capability: generate_key_info\n    execution:\n      runtime: pi\n`

function makeStudio(overrides: Record<string, unknown> = {}) {
  return {
    originalYaml: baselineYaml,
    runtimeChangedKeys: [],
    runtimeSaveUnavailable: false,
    runtimeSaving: false,
    lastRuntimeSavedKeys: [],
    saveRuntimeConfig: vi.fn(() => Promise.resolve(true)),
    rollbackRuntimeConfig: vi.fn(() => Promise.resolve(true)),
    ...overrides,
  }
}

function renderSection(
  props: Partial<
    React.ComponentProps<typeof WorkflowNodeRuntimeChannelSection>
  > = {},
  studio: object | null = makeStudio()
) {
  const content = (
    <MemoryRouter initialEntries={['/workspaces/ws1/studio']}>
      <Routes>
        <Route
          path="/workspaces/:workspaceId/studio"
          element={
            <WorkflowNodeRuntimeChannelSection
              node={node}
              definitionYaml={baselineYaml}
              setDefinitionYaml={() => {}}
              {...props}
            />
          }
        />
      </Routes>
    </MemoryRouter>
  )
  return render(
    <QueryClientProvider client={new QueryClient()}>
      {studio
        ? withStudioProviders(studio, makeStudioView(), content)
        : content}
    </QueryClientProvider>
  )
}

describe('WorkflowNodeRuntimeChannelSection（M48 运行通道专区）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the runtime fields with channel badge and save bar', () => {
    renderSection()

    expect(screen.getByText('运行配置')).toBeInTheDocument()
    expect(screen.getByText('运行通道 · 即改即存')).toBeInTheDocument()
    expect(screen.getByLabelText('Provider')).toBeInTheDocument()
    expect(screen.getByLabelText('Model')).toBeInTheDocument()
    expect(screen.getByLabelText('Thinking')).toBeInTheDocument()
    expect(screen.getByLabelText('Prompt')).toBeInTheDocument()
    expect(screen.getByLabelText('Tools 覆盖')).toBeInTheDocument()
    expect(screen.getByLabelText('Requires labels')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /保存运行配置/ })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '版本历史' })).toBeInTheDocument()
  })

  it('keeps the save button disabled without runtime changes and enables it with changes', () => {
    const studio = makeStudio({
      runtimeChangedKeys: ['nodes.generate_key_info.model'],
    })
    renderSection({}, studio)

    expect(screen.getByRole('button', { name: /保存运行配置/ })).toBeEnabled()
  })

  it('disables the save button when nothing changed', () => {
    renderSection()

    expect(screen.getByRole('button', { name: /保存运行配置/ })).toBeDisabled()
  })

  it('saves through the runtime channel on click', () => {
    const studio = makeStudio({
      runtimeChangedKeys: ['nodes.generate_key_info.model'],
    })
    renderSection({}, studio)

    fireEvent.click(screen.getByRole('button', { name: /保存运行配置/ }))

    expect(studio.saveRuntimeConfig).toHaveBeenCalledWith()
  })

  it('marks fields differing from the active baseline as 已修改 · 待保存', () => {
    const draft = baselineYaml.replace(
      'execution:\n      runtime: pi\n',
      'execution:\n      runtime: pi\n      model: gpt-5\n'
    )
    renderSection({ definitionYaml: draft })

    expect(screen.getByText('已修改 · 待保存')).toBeInTheDocument()
  })

  it('shows 已生效 for fields covered by the last runtime save', () => {
    const studio = makeStudio({
      lastRuntimeSavedKeys: ['nodes.generate_key_info.model'],
    })
    renderSection({}, studio)

    expect(screen.getAllByText('已生效').length).toBeGreaterThan(0)
  })

  it('disables everything with a banner in read-only revision view', () => {
    renderSection({ readOnly: true })

    expect(
      screen.getByText(/查看历史版本时不可修改运行配置/)
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Model')).toBeDisabled()
    expect(screen.getByRole('button', { name: /保存运行配置/ })).toBeDisabled()
  })

  it('只读视图不挂「已修改 · 待保存」状态点（展示的是被查看版本）', () => {
    const draft = baselineYaml.replace(
      'execution:\n      runtime: pi\n',
      'execution:\n      runtime: pi\n      model: gpt-5\n'
    )
    renderSection({ definitionYaml: draft, readOnly: true })

    expect(screen.queryByText('已修改 · 待保存')).not.toBeInTheDocument()
  })

  it('选中态走 CSS module 类（r1 P2-3 回归：自定义分段高亮）', () => {
    const withPrompt = baselineYaml.replace(
      'execution:\n      runtime: pi\n',
      'execution:\n      runtime: pi\n      prompt: 自定义提示\n'
    )
    renderSection({ definitionYaml: withPrompt })

    const customButton = screen.getByRole('button', { name: '自定义' })
    const defaultButton = screen.getByRole('button', {
      name: '默认（随 skill）',
    })
    expect(customButton.className).toMatch(/active/)
    expect(defaultButton.className).not.toMatch(/active/)
  })

  it('r1 P1-1：只读视图下失效工具告警不渲染删除钮（点击面被门禁）', () => {
    const withInvalidTool = baselineYaml.replace(
      'execution:\n      runtime: pi\n',
      'execution:\n      runtime: pi\n    tools: [not_in_catalog]\n'
    )
    let nextYaml = ''
    renderSection({
      definitionYaml: withInvalidTool,
      readOnly: true,
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })

    expect(screen.getByText(/不在 runtime/)).toBeInTheDocument()
    // MUI Chip onDelete=undefined 时无删除图标按钮（chip 编辑器自身的 ×
    // 在只读态另行 disabled，二者是不同删除面）。
    expect(
      document.querySelector('.MuiChip-deleteIcon')
    ).not.toBeInTheDocument()
    expect(nextYaml).toBe('')
  })

  it('writes a node model override to workflow YAML', () => {
    let nextYaml = ''
    renderSection({
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })

    fireEvent.change(screen.getByLabelText('Model'), {
      target: { value: 'gpt-5' },
    })

    expect(nextYaml).toContain('model: gpt-5')
  })

  it('keeps a cleared provider empty instead of restoring the persisted value', () => {
    let nextYaml = ''
    const initialYaml = `execution:\n  provider: deepseek\nnodes:\n  generate_key_info:\n    capability: generate_key_info\n    execution:\n      runtime: pi\n      provider: deepseek\n`
    renderSection({
      definitionYaml: initialYaml,
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })

    fireEvent.change(screen.getByLabelText('Provider'), {
      target: { value: '' },
    })

    // 顶层 execution 默认保留在 YAML，节点级 provider（6 空格缩进）必须被移除。
    expect(nextYaml).not.toContain('      provider:')
  })

  it('offers datalist options from the runtime models of online workers', () => {
    renderSection()

    const providerInput = screen.getByLabelText('Provider') as HTMLInputElement
    const providerList = document.getElementById(
      providerInput.getAttribute('list')!
    ) as HTMLDataListElement
    expect(
      Array.from(providerList.options).map((option) => option.value)
    ).toEqual(['deepseek'])

    const modelInput = screen.getByLabelText('Model') as HTMLInputElement
    const modelList = document.getElementById(
      modelInput.getAttribute('list')!
    ) as HTMLDataListElement
    expect(Array.from(modelList.options).map((option) => option.value)).toEqual(
      ['your-model-b', 'your-model-c']
    )
  })

  it('shows the workflow thinking default on the empty option', () => {
    renderSection()

    const thinkingSelect = screen.getByLabelText(
      'Thinking'
    ) as HTMLSelectElement
    expect(thinkingSelect.options[0].textContent).toBe(
      '继承 workflow 默认（low）'
    )
  })

  it('adds and removes tools through the chip editor', () => {
    let nextYaml = ''
    renderSection({
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })

    fireEvent.click(screen.getByRole('button', { name: /添加工具/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'web_search' }))
    expect(nextYaml).toContain('- web_search')

    nextYaml = ''
    const withTools = `${baselineYaml.replace('execution:\n      runtime: pi\n', 'execution:\n      runtime: pi\n')}    tools: [web_search]\n`
    renderSection({
      definitionYaml: withTools,
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })
    fireEvent.click(screen.getByRole('button', { name: '移除 web_search' }))
    expect(nextYaml).not.toContain('web_search')
  })

  it('adds requires labels as key=value chips', () => {
    let nextYaml = ''
    renderSection({
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })

    fireEvent.click(screen.getByRole('button', { name: /添加标签/ }))
    fireEvent.change(screen.getByLabelText('添加标签'), {
      target: { value: 'gpu=a100' },
    })
    fireEvent.keyDown(screen.getByLabelText('添加标签'), { key: 'Enter' })

    expect(nextYaml).toContain('gpu: a100')
  })

  it('clears the custom prompt when switching back to 默认（随 skill）', () => {
    let nextYaml = ''
    const withPrompt = baselineYaml.replace(
      'execution:\n      runtime: pi\n',
      'execution:\n      runtime: pi\n      prompt: 自定义提示\n      prompt_mode: overwrite\n'
    )
    renderSection({
      definitionYaml: withPrompt,
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })

    fireEvent.click(screen.getByRole('button', { name: '默认（随 skill）' }))

    expect(nextYaml).not.toContain('自定义提示')
    expect(nextYaml).not.toContain('prompt_mode')
  })

  it('enables the prompt textarea on 自定义 without writing YAML', () => {
    let nextYaml = ''
    renderSection({
      setDefinitionYaml: (value: string) => {
        nextYaml = value
      },
    })

    expect(screen.getByLabelText('Prompt')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '自定义' }))

    expect(screen.getByLabelText('Prompt')).toBeEnabled()
    // 未输入前不写草稿（运行配置 diff 为空，保存按钮保持禁用）。
    expect(nextYaml).toBe('')
  })

  it('renders nothing for nodes without a self-contained profile', () => {
    const { container } = renderSection({
      definitionYaml: `nodes:\n  generate_key_info:\n    capability: generate_key_info\n`,
    })

    expect(container.querySelector('[aria-label="运行配置"]')).toBeNull()
  })
})
