import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentRoutingSection } from './AgentRoutingSection'
import { useSettingStore } from '../stores/settingStore'

const executorConfiguration = {
  allocations: [],
  bindings: [],
  node_limits: [],
  migration_warnings: [],
  agent_capacity: 4,
}

const settings = {
  entityType: 'video',
  workflowKey: 'demo_video_workflow',
  resources: {},
}

describe('AgentRoutingSection', () => {
  beforeEach(() => {
    useSettingStore.setState({
      settings,
      originalSettings: settings,
      executorConfiguration,
      originalExecutorConfiguration: executorConfiguration,
      isDirty: false,
    })
  })

  it('renders the workspace-level capacity input with the stored value', () => {
    render(<AgentRoutingSection />)
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
    render(<AgentRoutingSection />)
    expect(screen.getByLabelText('Agent 并发上限')).toHaveValue(null)
  })

  it('propagates a valid integer capacity to the store and marks dirty', () => {
    render(<AgentRoutingSection />)
    fireEvent.change(screen.getByLabelText('Agent 并发上限'), {
      target: { value: '8' },
    })
    const state = useSettingStore.getState()
    expect(state.executorConfiguration.agent_capacity).toBe(8)
    expect(state.isDirty).toBe(true)
  })

  it('ignores invalid capacity input and snaps back on blur', () => {
    render(<AgentRoutingSection />)
    const input = screen.getByLabelText('Agent 并发上限')
    fireEvent.change(input, { target: { value: '0' } })
    fireEvent.change(input, { target: { value: 'abc' } })
    expect(
      useSettingStore.getState().executorConfiguration.agent_capacity
    ).toBe(4)
    fireEvent.blur(input)
    expect(input).toHaveValue(4)
  })
})
