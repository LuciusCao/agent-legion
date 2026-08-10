import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { IntakeConfigSection } from './IntakeConfigSection'
import { WorkflowSection } from './WorkflowSection'
import { ConnectionTestStatus } from './ConnectionTestStatus'
import { fetchWorkflows } from '../../api'
import type { WorkspaceSettings, WorkflowDefinitionRecord } from '../../types'
import type { TestStatus } from '../../stores/settingStore'

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    fetchWorkflows: vi.fn(),
  }
})

const mockFetchWorkflows = vi.mocked(fetchWorkflows)

const baseSettings: WorkspaceSettings = {
  entityType: 'question',
  intakeModes: ['manual'],
  labelOverrides: {},
  workflowKey: 'question_content',
}

const workflowDefinition: WorkflowDefinitionRecord = {
  key: 'question_content',
  label: 'Question Content',
  intake: {
    modes: [
      {
        key: 'manual',
        label: 'Manual',
        input_field: 'id_list',
      },
      { key: 'auto', label: 'Auto', input_field: 'source' },
    ],
  },
  edges: [],
  nodes: [],
}

const idleStatus: TestStatus = { state: 'idle', message: '' }

describe('IntakeConfigSection', () => {
  const mockSetSettings = vi.fn()
  const mockTestConnection = vi.fn()

  beforeEach(() => {
    mockSetSettings.mockReset()
    mockTestConnection.mockReset()
  })

  it('changes entity type', async () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    const select = screen.getByRole('combobox', { name: '默认实体类型' })
    await act(async () => {
      fireEvent.mouseDown(select)
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('option', { name: 'knowledge' }))
    })

    expect(mockSetSettings).toHaveBeenCalledWith({ entityType: 'knowledge' })
  })

  it('toggles an intake mode on', () => {
    render(
      <IntakeConfigSection
        settings={{ ...baseSettings, intakeModes: [] }}
        workflowDefinition={{
          ...workflowDefinition,
          intake: {
            modes: [{ key: 'none', label: 'None', input_field: 'x' }],
          },
        }}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    const checkbox = document.querySelector(
      'input[type="checkbox"]'
    ) as HTMLInputElement
    fireEvent.click(checkbox)

    expect(mockSetSettings).toHaveBeenCalledWith({ intakeModes: ['none'] })
  })

  it('toggles an intake mode and only sends intakeModes', () => {
    render(
      <IntakeConfigSection
        settings={{ ...baseSettings, intakeModes: [] }}
        workflowDefinition={workflowDefinition}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    const checkbox = document.querySelector(
      'input[type="checkbox"]'
    ) as HTMLInputElement
    fireEvent.click(checkbox)

    expect(mockSetSettings).toHaveBeenCalledWith({
      intakeModes: ['manual'],
    })
  })

  it('unchecks an intake mode and only sends intakeModes', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    const checkbox = document.querySelector(
      'input[type="checkbox"]'
    ) as HTMLInputElement
    fireEvent.click(checkbox)

    expect(mockSetSettings).toHaveBeenCalledWith({
      intakeModes: [],
    })
  })

  it('triggers connection test and disables button while testing', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        testStatus={idleStatus}
        saveError={null}
        isTesting={true}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    expect(screen.getByText('测试中...')).toBeDisabled()
  })

  it('also disables test button while saving', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={true}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    expect(screen.getByText('测试连接')).toBeDisabled()
  })

  it('shows save error when provided', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        testStatus={idleStatus}
        saveError="保存失败"
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    expect(screen.getByText('保存失败')).toBeInTheDocument()
  })

  it('handles workflow definition without intake modes', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={{ ...workflowDefinition, intake: { modes: [] } }}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    expect(screen.queryByText('Manual')).not.toBeInTheDocument()
  })
})

describe('WorkflowSection', () => {
  beforeEach(() => {
    mockFetchWorkflows.mockReset()
  })

  it('loads workflow options and selects a value', async () => {
    mockFetchWorkflows.mockResolvedValue({
      workflows: [
        { key: 'q1', label: 'Q1' },
        { key: 'q2', label: 'Q2' },
      ],
    })

    const onChange = vi.fn()
    render(<WorkflowSection workflowKey="" onChange={onChange} />)

    const select = screen.getByRole('combobox', { name: '工作流' })
    await act(async () => {
      fireEvent.mouseDown(select)
    })
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'Q2' })).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByRole('option', { name: 'Q2' }))
    })

    expect(onChange).toHaveBeenCalledWith('q2')
  })

  it('falls back to empty options and shows an error when fetch fails', async () => {
    mockFetchWorkflows.mockRejectedValue(new Error('network error'))

    render(<WorkflowSection workflowKey="" onChange={vi.fn()} />)

    // Assert before opening the menu: MUI aria-hides the rest of the tree
    // while the dropdown is open, which would hide the alert from queries.
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        '工作流列表加载失败，请刷新重试'
      )
    })

    const select = screen.getByRole('combobox', { name: '工作流' })
    await act(async () => {
      fireEvent.mouseDown(select)
    })

    expect(screen.getByRole('option', { name: '请选择' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Q1' })).not.toBeInTheDocument()
  })
})

describe('ConnectionTestStatus', () => {
  it('renders nothing when idle', () => {
    const { container } = render(<ConnectionTestStatus state="idle" />)
    expect(container.firstChild).toBeNull()
  })

  it('renders testing state', () => {
    render(<ConnectionTestStatus state="testing" />)
    expect(screen.getByText('测试中...')).toHaveClass('running')
  })

  it('renders success state with message', () => {
    render(<ConnectionTestStatus state="success" message="ok" />)
    expect(screen.getByText('连接成功 · ok')).toHaveClass('status-badge')
  })

  it('renders failed state without message', () => {
    render(<ConnectionTestStatus state="failed" />)
    expect(screen.getByText('连接失败')).toHaveClass('failed')
  })
})
