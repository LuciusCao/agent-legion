import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AgentRoutingSection } from './AgentRoutingSection'
import { useSettingStore } from '../stores/settingStore'
import { expectConsoleWarning } from '../test-setup'

const route = {
  workflow_key: 'video_knowledge',
  node_key: 'subtitle_review',
  node_label: '字幕校对',
  capability: 'review_subtitles',
  agent_id: 'video-subtitle-review-v1',
  agent_skill: 'video_knowledge/review_subtitles',
}

const executorConfiguration = {
  allocations: [],
  bindings: [],
  node_limits: [],
  migration_warnings: [],
  agent_capacity: 4,
}

const settings = {
  entityType: 'video',
  intakeModes: [],
  labelOverrides: {},
  workflowKey: 'video_knowledge',
  resources: {},
}

function renderSection() {
  return render(
    <MemoryRouter>
      <AgentRoutingSection />
    </MemoryRouter>
  )
}

describe('AgentRoutingSection', () => {
  beforeEach(() => {
    expectConsoleWarning(/React Router Future Flag Warning/)
    useSettingStore.setState({
      agentRoutes: [route],
      settings,
      originalSettings: settings,
      workspaceId: 'video_knowledge',
      executorConfiguration,
      originalExecutorConfiguration: executorConfiguration,
      isDirty: false,
    })
  })

  it('renders the workspace-level capacity input with the stored value', () => {
    renderSection()
    const input = screen.getByLabelText('Agent 并发上限')
    expect(input).toHaveValue(4)
    expect(
      screen.getByText(/该上限约束本 workspace 全部 Agent 节点跨所有 Worker/)
    ).toBeInTheDocument()
  })

  it('renders an empty capacity input when the cap is unset', () => {
    useSettingStore.setState({
      executorConfiguration: { ...executorConfiguration, agent_capacity: null },
    })
    renderSection()
    expect(screen.getByLabelText('Agent 并发上限')).toHaveValue(null)
  })

  it('propagates a valid integer capacity to the store and marks dirty', () => {
    renderSection()
    fireEvent.change(screen.getByLabelText('Agent 并发上限'), {
      target: { value: '8' },
    })
    const state = useSettingStore.getState()
    expect(state.executorConfiguration.agent_capacity).toBe(8)
    expect(state.isDirty).toBe(true)
  })

  it('ignores invalid capacity input and snaps back on blur', () => {
    renderSection()
    const input = screen.getByLabelText('Agent 并发上限')
    fireEvent.change(input, { target: { value: '0' } })
    fireEvent.change(input, { target: { value: 'abc' } })
    expect(
      useSettingStore.getState().executorConfiguration.agent_capacity
    ).toBe(4)
    fireEvent.blur(input)
    expect(input).toHaveValue(4)
  })

  it('renders agent routes without a node-level concurrency chip', () => {
    renderSection()
    const item = screen.getByTestId('agent-route-subtitle_review')
    expect(item.textContent).toContain('字幕校对')
    expect(item.textContent).toContain('review_subtitles')
    expect(item.textContent).toContain('video-subtitle-review-v1')
    expect(item.textContent).not.toContain('节点并发上限')
    expect(
      screen.getByText(/并发上限为 workspace 级，见上方设置/)
    ).toBeInTheDocument()
  })

  it('shows empty state when the workflow has no agent nodes', () => {
    useSettingStore.setState({ agentRoutes: [] })
    renderSection()
    expect(screen.getByText('当前 workflow 没有 Agent 节点')).toBeTruthy()
  })
})
