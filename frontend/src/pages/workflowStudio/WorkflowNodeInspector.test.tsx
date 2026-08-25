import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import { api } from '../../api'
import { useSettingStore } from '../../stores/settingStore'
import { createTestQueryClient } from '../../testing/testQueryClient'

vi.mock('../../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    QueryClientProvider,
    { client: createTestQueryClient() },
    children
  )
}

const draftYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '  intake:',
  '    label: 读取知识点',
  '    capability: intake',
  '    after: [_start]',
  'edges:',
  '  - source: _start',
  '    target: intake',
  '',
].join('\n')

function renderInspector(selectedNodeKey: string | null) {
  return render(
    <WorkflowNodeInspector
      workflow={null}
      agentCatalog={[]}
      selectedNodeKey={selectedNodeKey}
      definitionYaml={draftYaml}
      setDefinitionYaml={() => {}}
      onClose={() => {}}
    />,
    { wrapper }
  )
}

describe('WorkflowNodeInspector for draft-only (ghost) nodes', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      origin: 'none',
      code: '',
      has_draft: false,
      draft_code: null,
      draft_version: null,
    })
    useSettingStore.setState({ workspaceId: 'ws1' })
  })

  it('renders the editable sections for a ghost executable node', async () => {
    renderInspector('intake')

    // 基本设置（YAML 结构化编辑）可用。
    expect(await screen.findByLabelText('节点名称')).toHaveValue('读取知识点')
    expect(screen.getByLabelText('能力')).toHaveValue('intake')
    // 执行能力 / 数据契约 / 依赖段齐全。
    expect(screen.getByLabelText('节点执行能力')).toBeInTheDocument()
    expect(screen.getByText('数据契约')).toBeInTheDocument()
    expect(screen.getByText('依赖关系')).toBeInTheDocument()
    // 不再是「选择一个节点」空态。
    expect(screen.queryByText('选择一个节点')).not.toBeInTheDocument()
  })

  it('keeps the read-only entry contract for a ghost start node', () => {
    renderInspector('_start')

    expect(screen.getByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.queryByLabelText('节点名称')).not.toBeInTheDocument()
  })

  it('falls back for keys absent from both baseline and draft', () => {
    // workflow 为 null（空态）且草稿里也没有：保持「未加载 workflow」语义。
    renderInspector('no_such_node')

    expect(screen.getByText('未加载 workflow')).toBeInTheDocument()
  })
})
