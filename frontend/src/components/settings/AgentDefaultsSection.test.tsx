import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { updateAgentDefaults } from '../../api'
import { AgentDefaultsSection } from './AgentDefaultsSection'

vi.mock('../../api', () => ({
  updateAgentDefaults: vi.fn(),
}))

const mockUpdate = vi.mocked(updateAgentDefaults)

function renderSection(
  agentDefaults = { provider: 'deepseek', model: 'deepseek-v4-flash', thinking: '' }
) {
  const onSaved = vi.fn()
  render(
    <AgentDefaultsSection
      workspaceId="ws1"
      agentDefaults={agentDefaults}
      onSaved={onSaved}
    />
  )
  return { onSaved }
}

describe('AgentDefaultsSection', () => {
  beforeEach(() => {
    mockUpdate.mockReset()
  })

  it('renders the current defaults and explains the override order', () => {
    renderSection()

    expect(screen.getByLabelText('Provider')).toHaveValue('deepseek')
    expect(screen.getByLabelText('Model')).toHaveValue('deepseek-v4-flash')
    expect(screen.getByText(/节点级覆盖优先/)).toBeInTheDocument()
    expect(screen.getByText(/无法入队/)).toBeInTheDocument()
  })

  it('saves through PATCH agent-defaults and notifies the parent', async () => {
    mockUpdate.mockResolvedValue({ settings: {} })
    const { onSaved } = renderSection()

    fireEvent.change(screen.getByLabelText('Thinking'), {
      target: { value: 'low' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    const expected = { provider: 'deepseek', model: 'deepseek-v4-flash', thinking: 'low' }
    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('ws1', expected)
    )
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expected))
  })

  it('shows the error when saving fails', async () => {
    mockUpdate.mockRejectedValue(new Error('HTTP 400'))
    renderSection()

    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 400')
  })
})
