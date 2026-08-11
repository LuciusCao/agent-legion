import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { NodeConfigCard } from './NodeConfigCard'
import { api } from '../../api'
import { TestQueryProvider } from '../../testing/testQueryClient'
import type { ConfigSchema } from '../../types'

vi.mock('../../api', () => ({
  api: vi.fn(),
}))

const mockApi = vi.mocked(api)

const schema: ConfigSchema = {
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
}

function renderCard(overrides?: Partial<Parameters<typeof NodeConfigCard>[0]>) {
  return render(
    <TestQueryProvider>
      <NodeConfigCard
        workspaceId="ws1"
        nodeKey="review_keywords"
        label="审核关键词"
        schema={schema}
        initialValues={{ page_size: 20 }}
        {...overrides}
      />
    </TestQueryProvider>
  )
}

function lastCallBody(): unknown {
  const init = mockApi.mock.calls[mockApi.mock.calls.length - 1]?.[1]
  return JSON.parse(String(init?.body))
}

describe('NodeConfigCard', () => {
  beforeEach(() => {
    mockApi.mockReset()
    mockApi.mockResolvedValue({
      settings: {
        nodeConfig: { review_keywords: { page_size: 50 } },
        nodeConfigSchemas: { review_keywords: schema },
      },
    })
  })

  it('renders a schema-driven form initialized from initialValues', () => {
    renderCard()

    expect(screen.getByText('审核关键词')).toBeInTheDocument()
    expect(screen.getByText('review_keywords')).toBeInTheDocument()
    expect(
      screen.getByRole('spinbutton', { name: 'page_size' })
    ).toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'page_size' })).toHaveValue(
      20
    )
  })

  it('renders executor node fields from string-only schemas', () => {
    // Executor capability schemas (spec D15) declare non-secret string
    // parameters without defaults; the card must render them like any
    // other node card.
    renderCard({
      nodeKey: 'fetch_questions',
      label: 'fetch_questions',
      schema: {
        type: 'object',
        properties: {
          bank_version: { type: 'string', description: '题库版本' },
        },
      },
      initialValues: {},
    })

    expect(screen.getByRole('textbox', { name: 'bank_version' })).toHaveValue(
      ''
    )
  })

  it('sends the current values on save', async () => {
    renderCard()

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
    renderCard()

    fireEvent.click(screen.getByRole('button', { name: '清除覆盖' }))

    await waitFor(() => expect(mockApi).toHaveBeenCalled())
    expect(lastCallBody()).toEqual({
      nodeConfig: { review_keywords: {} },
    })
  })

  it('shows an error message when the save fails', async () => {
    mockApi.mockRejectedValue(new Error('校验失败'))
    renderCard()

    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('校验失败')
    )
  })
})
