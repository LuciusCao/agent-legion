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
    const badge = screen.getByTestId('dag-node-executor-badge')
    expect(badge).toHaveTextContent('abc123de')
    expect(badge).toHaveAttribute(
      'title',
      'key-info-generator / worker-abc123def456'
    )
  })

  it('falls back to executorId when workerId is missing', () => {
    renderWithProvider({
      ...baseData,
      executorId: 'local-default',
      workerId: null,
    })
    const badge = screen.getByTestId('dag-node-executor-badge')
    expect(badge).toHaveTextContent('local-default')
    expect(badge).toHaveAttribute('title', 'local-default')
  })

  it('renders the Agent while a request is queued without a Worker', () => {
    renderWithProvider({ ...baseData, agentId: 'key-info-generator' })
    expect(screen.getByTestId('dag-node-executor-badge')).toHaveTextContent(
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
      screen.queryByTestId('dag-node-executor-badge')
    ).not.toBeInTheDocument()
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
})
