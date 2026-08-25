import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
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

function renderInspector(
  selectedNodeKey: string | null,
  options?: {
    definitionYaml?: string
    compareSummary?: ChangeSummaryViewModel | null
  }
) {
  return render(
    <WorkflowNodeInspector
      workflow={null}
      agentCatalog={[]}
      selectedNodeKey={selectedNodeKey}
      definitionYaml={options?.definitionYaml ?? draftYaml}
      setDefinitionYaml={() => {}}
      compareSummary={options?.compareSummary}
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

// 草稿 YAML 没有 start 节点：后端 loader 注入的合成 _start 不在 YAML 文本里，
// compare 把它画成 added ghost——inspector 走 compareSummary 兜底。
const yamlWithoutStart = [
  'key: demo',
  'nodes:',
  '  intake:',
  '    label: 读取知识点',
  '    capability: intake',
  '',
].join('\n')

function makeCompareSummary(
  nodeType: 'start' | 'node'
): ChangeSummaryViewModel {
  return {
    createsRevision: true,
    riskLevel: 'info',
    severityLabel: '提示',
    nodeChanges: [
      {
        type: 'added',
        nodeKey: '_start',
        label: 'Start',
        nodeType,
        fields: [],
        severity: 'info',
      },
    ],
    edgeChanges: [
      {
        type: 'added',
        source: '_start',
        target: 'intake',
        beforeCondition: null,
        afterCondition: null,
        severity: 'info',
      },
    ],
    intakeChanges: [],
    metadataChanges: [],
    riskFlags: [],
    changedNodeKeys: new Set(['_start', 'intake']),
  }
}

describe('WorkflowNodeInspector compare fallback for a synthetic start ghost', () => {
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

  it('renders the editable entry contract from the compare summary', async () => {
    renderInspector('_start', {
      definitionYaml: yamlWithoutStart,
      compareSummary: makeCompareSummary('start'),
    })

    expect(await screen.findByLabelText('入口节点')).toBeInTheDocument()
    expect(screen.queryByText('未加载 workflow')).not.toBeInTheDocument()
    // 默认契约 material/ref（与 acceptedItemTypes fallback 语义一致）。
    expect(
      screen.getByRole('checkbox', { name: '材料文件 material' })
    ).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '外部引用 ref' })).toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: '文件夹 bundle' })
    ).not.toBeChecked()
    // added 边从 edgeChanges 还原进依赖关系段。
    expect(screen.getByText('0 入 / 1 出')).toBeInTheDocument()
  })

  it('does not synthesize details for a non-start node change', () => {
    renderInspector('_start', {
      definitionYaml: yamlWithoutStart,
      compareSummary: makeCompareSummary('node'),
    })

    expect(screen.getByText('未加载 workflow')).toBeInTheDocument()
  })

  it('keeps the draft YAML resolution ahead of the compare fallback', async () => {
    // draftYaml 里有 _start（无 accepted_item_types）：YAML 路径优先，
    // compare 兜底不得覆盖它（否则三个 checkbox 会按默认契约被勾上）。
    renderInspector('_start', {
      definitionYaml: draftYaml,
      compareSummary: makeCompareSummary('start'),
    })

    expect(await screen.findByLabelText('入口节点')).toBeInTheDocument()
    expect(
      screen.getByRole('checkbox', { name: '材料文件 material' })
    ).not.toBeChecked()
  })
})
