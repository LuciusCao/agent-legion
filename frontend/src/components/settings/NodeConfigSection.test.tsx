import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { NodeConfigSection } from './NodeConfigSection'
import { api } from '../../api'
import type { WorkspaceSettings } from '../../types'

vi.mock('../../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

const baseSettings: WorkspaceSettings = {
  entityType: 'question',
  intakeModes: [],
  labelOverrides: {},
  workflowKey: '',
  resources: {},
  nodeConfig: {
    review_keywords: { page_size: 20 },
  },
  nodeConfigSchemas: {
    review_keywords: {
      type: 'object',
      properties: {
        page_size: {
          type: 'integer',
          default: 100,
          minimum: 1,
          maximum: 500,
          description: '每页数量',
        },
      },
    },
  },
}

function lastCallBody(): unknown {
  const init = mockApi.mock.calls[mockApi.mock.calls.length - 1]?.[1]
  return JSON.parse(String(init?.body))
}

describe('NodeConfigSection', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      settings: {
        nodeConfig: { review_keywords: { page_size: 50 } },
        nodeConfigSchemas: baseSettings.nodeConfigSchemas,
      },
    })
  })

  it('renders nothing when nodeConfigSchemas is empty', () => {
    const { container } = render(
      <NodeConfigSection
        workspaceId="ws1"
        settings={{ ...baseSettings, nodeConfigSchemas: {} }}
      />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders a schema-driven form initialized from nodeConfig', () => {
    render(<NodeConfigSection workspaceId="ws1" settings={baseSettings} />)

    expect(screen.getByText('节点配置')).toBeInTheDocument()
    expect(screen.getByText('review_keywords')).toBeInTheDocument()
    expect(
      screen.getByRole('spinbutton', { name: 'page_size' })
    ).toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'page_size' })).toHaveValue(
      20
    )
  })

  it('uses the workflow node label when available', () => {
    render(
      <NodeConfigSection
        workspaceId="ws1"
        settings={baseSettings}
        workflowDefinition={{
          key: 'question_content',
          label: 'Question Content',
          intake: { modes: [] },
          edges: [],
          nodes: [
            {
              key: 'review_keywords',
              label: '审核关键词',
              capability: 'review_keywords',
              after: [],
              inputs: [],
              outputs: [],
            },
          ],
        }}
      />
    )

    expect(screen.getByText('审核关键词')).toBeInTheDocument()
  })

  it('renders executor node cards from string-only schemas', () => {
    // Executor capability schemas (spec D15) declare non-secret string
    // parameters without defaults; the section must render them like any
    // other node card.
    const settings: WorkspaceSettings = {
      ...baseSettings,
      nodeConfig: {},
      nodeConfigSchemas: {
        fetch_questions: {
          type: 'object',
          properties: {
            bank_version: { type: 'string', description: '题库版本' },
          },
        },
      },
    }
    render(<NodeConfigSection workspaceId="ws1" settings={settings} />)

    expect(screen.getByText('fetch_questions')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'bank_version' })).toHaveValue(
      ''
    )
  })

  it('sends the current values on save', async () => {
    render(<NodeConfigSection workspaceId="ws1" settings={baseSettings} />)

    fireEvent.change(screen.getByRole('spinbutton', { name: 'page_size' }), {
      target: { value: '50' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockApi).toHaveBeenCalled())
    const [path, init] = mockApi.mock.calls[0]
    expect(path).toBe('/api/workspaces/ws1/settings/nodes')
    expect(init?.method).toBe('PATCH')
    expect(lastCallBody()).toEqual({
      nodeConfig: { review_keywords: { page_size: 50 } },
    })
    // Integer values must stay numbers for the strict backend validation.
    const body = lastCallBody() as {
      nodeConfig: Record<string, Record<string, unknown>>
    }
    expect(typeof body.nodeConfig.review_keywords.page_size).toBe('number')
  })

  it('sends an empty object to clear overrides', async () => {
    render(<NodeConfigSection workspaceId="ws1" settings={baseSettings} />)

    fireEvent.click(screen.getByRole('button', { name: '清除覆盖' }))

    await waitFor(() => expect(mockApi).toHaveBeenCalled())
    expect(lastCallBody()).toEqual({
      nodeConfig: { review_keywords: {} },
    })
  })

  it('shows an error message when the save fails', async () => {
    mockApi.mockRejectedValue(new Error('校验失败'))
    render(<NodeConfigSection workspaceId="ws1" settings={baseSettings} />)

    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('校验失败')
    )
  })
})
