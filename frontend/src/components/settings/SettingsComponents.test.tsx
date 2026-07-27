import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { IntakeConfigSection } from './IntakeConfigSection'
import { WorkflowSection } from './WorkflowSection'
import { ResourceProviderCard } from './ResourceProviderCard'
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
  resources: {
    cms: { enabled: true, config: { url: 'http://cms.test' } },
  },
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
        resourceProviders={[]}
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
        resourceProviders={[]}
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

  it('toggles an intake mode without touching resource bindings', () => {
    render(
      <IntakeConfigSection
        settings={{ ...baseSettings, intakeModes: [], resources: {} }}
        workflowDefinition={workflowDefinition}
        resourceProviders={[]}
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

  it('unchecks an intake mode without touching resource bindings', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        resourceProviders={[]}
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

  it('shows resource provider cards for active resources', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        resourceProviders={[
          {
            key: 'cms',
            provider: 'CMS',
            path: '/api/cms',
            defaultParams: { url: 'http://default' },
            paramKeys: ['url', 'token'],
          },
        ]}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    expect(screen.getByText('CMS')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'url' })).toHaveValue(
      'http://cms.test'
    )
  })

  it('renders resource provider cards even when no intake mode is selected (issue 024)', () => {
    render(
      <IntakeConfigSection
        settings={{ ...baseSettings, intakeModes: [] }}
        workflowDefinition={workflowDefinition}
        resourceProviders={[
          {
            key: 'cms',
            provider: 'CMS',
            path: '/api/cms',
            defaultParams: {},
            paramKeys: ['url'],
          },
        ]}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    expect(screen.getByText('CMS')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'url' })).toBeInTheDocument()
  })

  it('updates resource config when provider card input changes', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        resourceProviders={[
          {
            key: 'cms',
            provider: 'CMS',
            path: '/api/cms',
            defaultParams: {},
            paramKeys: ['url', 'token'],
          },
        ]}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    const tokenInput = screen.getByRole('textbox', { name: 'token' })
    fireEvent.change(tokenInput, { target: { value: 'secret' } })

    expect(mockSetSettings).toHaveBeenCalledWith({
      resources: {
        cms: {
          enabled: true,
          config: { url: 'http://cms.test', token: 'secret' },
        },
      },
    })
  })

  it('removes resource config value when input is cleared', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        resourceProviders={[
          {
            key: 'cms',
            provider: 'CMS',
            path: '/api/cms',
            defaultParams: {},
            paramKeys: ['url'],
          },
        ]}
        testStatus={idleStatus}
        saveError={null}
        isTesting={false}
        isSaving={false}
        setSettings={mockSetSettings}
        onTestConnection={mockTestConnection}
      />
    )

    const urlInput = screen.getByRole('textbox', { name: 'url' })
    fireEvent.change(urlInput, { target: { value: '' } })

    expect(mockSetSettings).toHaveBeenCalledWith({
      resources: {
        cms: {
          enabled: true,
          config: {},
        },
      },
    })
  })

  it('triggers connection test and disables button while testing', () => {
    render(
      <IntakeConfigSection
        settings={baseSettings}
        workflowDefinition={workflowDefinition}
        resourceProviders={[]}
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
        resourceProviders={[]}
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
        resourceProviders={[]}
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
        resourceProviders={[]}
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

  it('falls back to empty options when fetch fails', async () => {
    mockFetchWorkflows.mockRejectedValue(new Error('network error'))

    render(<WorkflowSection workflowKey="" onChange={vi.fn()} />)

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

describe('ResourceProviderCard', () => {
  it('renders paramKeys inputs and fires whole-config change events', () => {
    const onConfigChange = vi.fn()
    render(
      <ResourceProviderCard
        provider={{
          key: 'cms',
          provider: 'CMS',
          path: '/api/cms',
          defaultParams: { url: 'http://default' },
          paramKeys: ['url', 'token'],
        }}
        binding={{ enabled: true, config: { url: 'http://cms.test' } }}
        onConfigChange={onConfigChange}
      />
    )

    expect(screen.getByText('CMS')).toBeInTheDocument()
    expect(screen.getByText('Path: /api/cms')).toBeInTheDocument()

    const urlInput = screen.getByRole('textbox', { name: 'url' })
    expect(urlInput).toHaveValue('http://cms.test')

    fireEvent.change(urlInput, { target: { value: 'http://new.test' } })
    expect(onConfigChange).toHaveBeenCalledWith({ url: 'http://new.test' })
  })

  it('prefers config_schema and emits schema-typed values', () => {
    const onConfigChange = vi.fn()
    render(
      <ResourceProviderCard
        provider={{
          key: 'by_knowledge',
          provider: 'By Knowledge',
          path: '/api/by_knowledge',
          defaultParams: {},
          paramKeys: ['page_size'],
          config_schema: {
            type: 'object',
            properties: {
              page_size: { type: 'integer', default: 100 },
            },
          },
        }}
        binding={{ enabled: true, config: { page_size: 20 } }}
        onConfigChange={onConfigChange}
      />
    )

    const pageSizeInput = screen.getByRole('spinbutton', { name: 'page_size' })
    expect(pageSizeInput).toHaveValue(20)

    fireEvent.change(pageSizeInput, { target: { value: '50' } })
    expect(onConfigChange).toHaveBeenCalledWith({ page_size: 50 })
    expect(
      typeof (onConfigChange.mock.calls[0][0] as Record<string, unknown>)
        .page_size
    ).toBe('number')
  })
})
