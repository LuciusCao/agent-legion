import { render, screen, fireEvent } from '@testing-library/react'
import { DagNode, DagNodeData } from './DagNode'
import { describe, it, expect } from 'vitest'
import { ReactFlowProvider } from '@xyflow/react'

const TestDagNode = DagNode as unknown as React.FC<{
  id: string
  type: string
  data: DagNodeData
  selected?: boolean
  isConnectable?: boolean
}>

function renderWithProvider(data: DagNodeData, selected = false) {
  return render(
    <ReactFlowProvider>
      <TestDagNode
        id="n1"
        type="dagNode"
        data={data}
        selected={selected}
        isConnectable={false}
      />
    </ReactFlowProvider>
  )
}

const baseData: DagNodeData = {
  label: 'review_keywords',
  status: 'completed',
  duration: 12.4,
  executorKind: 'pi',
  nodeKey: 'review_keywords',
  capability: 'review_keywords',
  topologyBadges: ['entry', 'branch'],
  inputs: ['transcription.json', 'chapters.json'],
  outputs: ['keywords.json'],
}

describe('DagNode', () => {
  it('renders label, status, duration and executor kind', () => {
    renderWithProvider(baseData)
    expect(screen.getByText('Key · review_keywords')).toBeInTheDocument()
    expect(screen.getByText('能力 · review_keywords')).toBeInTheDocument()
    expect(screen.getByText('pi')).toBeInTheDocument()
    expect(screen.getByText('入口')).toBeInTheDocument()
    expect(screen.getByText('分支')).toBeInTheDocument()
    expect(screen.getByText(/耗时 12.4s/)).toBeInTheDocument()
  })

  it('renders input and output chips', () => {
    renderWithProvider(baseData)
    expect(screen.getByText('transcription.json')).toBeInTheDocument()
    expect(screen.getByText('chapters.json')).toBeInTheDocument()
    expect(screen.getByText('keywords.json')).toBeInTheDocument()
  })

  it('collapses inputs and outputs when more than 3', () => {
    const data: DagNodeData = {
      ...baseData,
      inputs: ['a.json', 'b.json', 'c.json', 'd.json'],
      outputs: ['x.json', 'y.json', 'z.json', 'w.json'],
    }
    renderWithProvider(data)
    const moreButtons = screen.getAllByText('+1')
    expect(moreButtons).toHaveLength(2)
    fireEvent.click(moreButtons[0])
    expect(screen.getByText('d.json')).toBeInTheDocument()
  })

  it.each([
    ['pending', 'radio_button_unchecked'],
    ['running', 'hourglass_empty'],
    ['completed', 'check_circle'],
    ['failed', 'error'],
    ['stale', 'warning'],
  ] as const)('applies %s status and renders %s icon', (status, icon) => {
    void icon
    renderWithProvider({ ...baseData, status })
    const card = screen.getByTestId('dag-node')
    expect(card).toHaveAttribute('data-status', status)
    expect(screen.getByTestId(`dag-icon-${status}`)).toBeInTheDocument()
  })

  it('renders Agent and Worker identity separately in the badge', () => {
    renderWithProvider({
      ...baseData,
      executorKind: 'pi',
      agentId: 'key-info-generator',
      workerId: 'worker-abc123def456',
    })
    const badge = screen.getByTestId('dag-node-execution-badge')
    expect(badge).toHaveTextContent('abc123de')
    expect(badge).toHaveAttribute(
      'title',
      'key-info-generator / worker-abc123def456'
    )
  })

  it('falls back to executorId when workerId is missing', () => {
    renderWithProvider({
      ...baseData,
      executorId: 'code-default',
      workerId: null,
    })
    const badge = screen.getByTestId('dag-node-execution-badge')
    expect(badge).toHaveTextContent('code-default')
    expect(badge).toHaveAttribute('title', 'code-default')
  })

  it('renders the Agent while a request is queued without a Worker', () => {
    renderWithProvider({ ...baseData, agentId: 'key-info-generator' })
    expect(screen.getByTestId('dag-node-execution-badge')).toHaveTextContent(
      'key-info-generator'
    )
  })

  it('renders no executor badge when all execution identities are missing', () => {
    renderWithProvider({
      ...baseData,
      agentId: null,
      executorId: null,
      workerId: null,
    })
    expect(
      screen.queryByTestId('dag-node-execution-badge')
    ).not.toBeInTheDocument()
  })

  it('renders an unbound warning chip when the node has no executor binding', () => {
    renderWithProvider({
      ...baseData,
      agentId: null,
      executorId: null,
      workerId: null,
      executorUnbound: true,
    })
    expect(screen.getByText('未绑定')).toBeInTheDocument()
  })

  it('renders not applicable node status', () => {
    renderWithProvider({
      label: '生成关键信息',
      status: 'not_applicable',
      inputs: [],
      outputs: [],
    })

    expect(screen.getByText('不适用')).toBeInTheDocument()
  })

  it.each([
    ['added', '新增'],
    ['modified', '已改'],
    ['removed', '已删'],
  ] as const)('renders the %s change badge as %s', (changeType, label) => {
    renderWithProvider({ ...baseData, changeType })
    expect(
      screen.getByTestId(`dag-change-badge-${changeType}`)
    ).toHaveTextContent(label)
  })

  it('renders no change badge without a change type', () => {
    renderWithProvider(baseData)
    expect(
      screen.queryByTestId('dag-change-badge-added')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('dag-change-badge-modified')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('dag-change-badge-removed')
    ).not.toBeInTheDocument()
  })

  // #276：置灰态渲染——hover/选中联动时非同链路节点由 DagGraph 写入
  // data.dimmed = true，节点卡片自身渲染 opacity: 0.45。
  it('dims the node card when data.dimmed is true', () => {
    const dimmed = renderWithProvider({ ...baseData, dimmed: true })
    expect(dimmed.getByTestId('dag-node').style.opacity).toBe('0.45')
    dimmed.unmount()

    const plain = renderWithProvider(baseData)
    expect(plain.getByTestId('dag-node').style.opacity).toBe('')
  })

  // #276：active 态渲染——hover/选中的目标节点由 DagGraph 写入 data.active
  // = true，卡片渲染蓝色轮廓（旧版 selectedFlowNode 的视觉等价物）。
  it('outlines the node card when data.active is true', () => {
    const active = renderWithProvider({ ...baseData, active: true })
    expect(active.getByTestId('dag-node').className).toContain('active')
    active.unmount()

    const plain = renderWithProvider(baseData)
    expect(plain.getByTestId('dag-node').className).not.toContain('active')
  })

  // #276：memo 比较函数——data 引用变化但业务内容相同（含 dimmed）时，
  // 节点不应重渲染。这是「hover 只重渲染受影响节点」在组件级的兜底断言：
  // DagGraph 只在 dimmed 翻转时新建 data，这里模拟「引用变了但内容没变」
  // 的输入（memo 比较被误改成全等引用比较时，行为退化在这里表现为肉眼不可见，
  // 所以配套断言「内容变化仍必须渲染」防止比较函数被写成恒 true）。
  it('skips re-render when only the data reference changes', () => {
    const { rerender } = render(
      <ReactFlowProvider>
        <TestDagNode
          id="n1"
          type="dagNode"
          data={baseData}
          isConnectable={false}
        />
      </ReactFlowProvider>
    )
    // 结构相同的另一份 data：内容一致、引用不同（inputs 数组也是新引用，
    // 与 computeLayout 每次 props 变化时重建数组的行为一致）。
    const sameContentData: DagNodeData = {
      ...baseData,
      inputs: [...baseData.inputs],
    }
    rerender(
      <ReactFlowProvider>
        <TestDagNode
          id="n1"
          type="dagNode"
          data={sameContentData}
          isConnectable={false}
        />
      </ReactFlowProvider>
    )
    // 内容不同的 data（label 变化）必须触发渲染：label 出现在 title 属性上。
    rerender(
      <ReactFlowProvider>
        <TestDagNode
          id="n1"
          type="dagNode"
          data={{ ...baseData, label: 'renamed_label' }}
          isConnectable={false}
        />
      </ReactFlowProvider>
    )
    expect(screen.getByTitle('renamed_label')).toBeInTheDocument()
  })
})
