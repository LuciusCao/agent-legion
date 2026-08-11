import { beforeEach, describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { SchemaConfigForm } from './SchemaConfigForm'
import type { ConfigSchema } from '../../types'
import { getConnections } from '../../api/connections'

vi.mock('../../api/connections', () => ({
  getConnections: vi.fn().mockResolvedValue({ connections: [] }),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

function renderForm(ui: ReactElement) {
  return render(<TestQueryProvider>{ui}</TestQueryProvider>)
}

describe('SchemaConfigForm', () => {
  it('renders a TextField for string properties and emits string values', () => {
    const onChange = vi.fn()
    const schema: ConfigSchema = {
      type: 'object',
      properties: { endpoint: { type: 'string' } },
    }
    renderForm(
      <SchemaConfigForm schema={schema} values={{}} onChange={onChange} />
    )

    const input = screen.getByRole('textbox', { name: 'endpoint' })
    fireEvent.change(input, { target: { value: 'http://api.test' } })
    expect(onChange).toHaveBeenCalledWith({ endpoint: 'http://api.test' })
  })

  it('emits numbers for integer properties and removes the key when cleared', () => {
    const onChange = vi.fn()
    const schema: ConfigSchema = {
      type: 'object',
      properties: {
        page_size: { type: 'integer', minimum: 1, maximum: 500 },
      },
    }
    renderForm(
      <SchemaConfigForm
        schema={schema}
        values={{ page_size: 20 }}
        onChange={onChange}
      />
    )

    const input = screen.getByRole('spinbutton', { name: 'page_size' })
    expect(input).toHaveValue(20)
    expect(screen.getByText('范围: 1 ~ 500')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '100' } })
    expect(onChange).toHaveBeenCalledWith({ page_size: 100 })
    const emitted = onChange.mock.calls[0][0] as Record<string, unknown>
    expect(typeof emitted.page_size).toBe('number')

    fireEvent.change(input, { target: { value: '' } })
    expect(onChange).toHaveBeenCalledWith({})
  })

  it('renders a Select for enum properties', async () => {
    const onChange = vi.fn()
    const schema: ConfigSchema = {
      type: 'object',
      properties: {
        mode: { type: 'string', enum: ['fast', 'slow'] },
      },
    }
    renderForm(
      <SchemaConfigForm schema={schema} values={{}} onChange={onChange} />
    )

    const select = screen.getByRole('combobox', { name: 'mode' })
    await act(async () => {
      fireEvent.mouseDown(select)
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('option', { name: 'fast' }))
    })
    expect(onChange).toHaveBeenCalledWith({ mode: 'fast' })
  })

  it('renders a password input for secret properties', () => {
    const schema: ConfigSchema = {
      type: 'object',
      properties: { api_key: { type: 'string', secret: true } },
    }
    renderForm(
      <SchemaConfigForm schema={schema} values={{}} onChange={vi.fn()} />
    )

    const input = screen.getByLabelText('api_key') as HTMLInputElement
    expect(input.type).toBe('password')
  })

  it('shows 已设置 for a masked secret marker without echoing a value', () => {
    const schema: ConfigSchema = {
      type: 'object',
      properties: { token: { type: 'string', secret: true } },
    }
    renderForm(
      <SchemaConfigForm
        schema={schema}
        values={{ token: { secret_set: true } }}
        onChange={vi.fn()}
      />
    )

    const input = screen.getByLabelText('token') as HTMLInputElement
    expect(input.type).toBe('password')
    expect(input.value).toBe('')
    expect(screen.getByText('已设置')).toBeInTheDocument()
  })

  it('shows 未设置 for an unset secret marker', () => {
    const schema: ConfigSchema = {
      type: 'object',
      properties: { token: { type: 'string', secret: true } },
    }
    renderForm(
      <SchemaConfigForm
        schema={schema}
        values={{ token: { secret_set: false } }}
        onChange={vi.fn()}
      />
    )

    expect(screen.getByText('未设置')).toBeInTheDocument()
  })

  it('emits the typed string for secrets, replacing the marker', () => {
    const onChange = vi.fn()
    const schema: ConfigSchema = {
      type: 'object',
      properties: { token: { type: 'string', secret: true } },
    }
    renderForm(
      <SchemaConfigForm
        schema={schema}
        values={{ token: { secret_set: true } }}
        onChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('token'), {
      target: { value: 'new-secret' },
    })
    expect(onChange).toHaveBeenCalledWith({ token: 'new-secret' })
  })

  it('keeps an explicit clear as an empty string instead of dropping the key', () => {
    const onChange = vi.fn()
    const schema: ConfigSchema = {
      type: 'object',
      properties: { token: { type: 'string', secret: true } },
    }
    renderForm(
      <SchemaConfigForm
        schema={schema}
        values={{ token: 'typed-value' }}
        onChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('token'), { target: { value: '' } })
    expect(onChange).toHaveBeenCalledWith({ token: '' })
  })

  it('uses the default as placeholder', () => {
    const schema: ConfigSchema = {
      type: 'object',
      properties: { timeout: { type: 'integer', default: 30 } },
    }
    renderForm(
      <SchemaConfigForm schema={schema} values={{}} onChange={vi.fn()} />
    )

    expect(screen.getByPlaceholderText('30')).toBeInTheDocument()
  })

  it('renders a Switch for boolean properties and emits booleans', () => {
    const onChange = vi.fn()
    const schema: ConfigSchema = {
      type: 'object',
      properties: { verbose: { type: 'boolean' } },
    }
    renderForm(
      <SchemaConfigForm schema={schema} values={{}} onChange={onChange} />
    )

    const toggle = screen.getByRole('checkbox', { name: 'verbose' })
    fireEvent.click(toggle)
    expect(onChange).toHaveBeenCalledWith({ verbose: true })
  })

  it('shows description as helper text', () => {
    const schema: ConfigSchema = {
      type: 'object',
      properties: {
        model: { type: 'string', description: 'LLM 模型名' },
      },
    }
    renderForm(
      <SchemaConfigForm schema={schema} values={{}} onChange={vi.fn()} />
    )

    expect(screen.getByText('LLM 模型名')).toBeInTheDocument()
  })

  it('renders a hint when the schema has no properties', () => {
    renderForm(<SchemaConfigForm schema={{}} values={{}} onChange={vi.fn()} />)
    expect(screen.getByText('无可配置参数')).toBeInTheDocument()
  })
})

describe('SchemaConfigForm connection datalist', () => {
  const connectionSchema: ConfigSchema = {
    type: 'object',
    properties: { connection: { type: 'string' } },
  }

  const connectionView = {
    key: 'lark-main',
    type: 'static_bearer',
    display_name: '飞书主租户',
    config: {},
    enabled: true,
    token: null,
  }

  it('offers connection key candidates via datalist', async () => {
    vi.mocked(getConnections).mockResolvedValue({
      connections: [connectionView],
    })
    const { container } = renderForm(
      <SchemaConfigForm
        schema={connectionSchema}
        values={{}}
        onChange={vi.fn()}
      />
    )

    // datalist id 由 useId 按实例生成，不再是全局常量。
    await waitFor(() => {
      expect(container.querySelectorAll('datalist option')).toHaveLength(1)
    })
    const datalist = container.querySelector('datalist') as HTMLElement
    expect(
      screen.getByRole('combobox', { name: 'connection' })
    ).toHaveAttribute('list', datalist.id)
  })

  it('shares one fetch and unique datalist ids across form instances', async () => {
    vi.mocked(getConnections).mockResolvedValue({
      connections: [connectionView],
    })
    const { container } = renderForm(
      <>
        <SchemaConfigForm
          schema={connectionSchema}
          values={{}}
          onChange={vi.fn()}
        />
        <SchemaConfigForm
          schema={connectionSchema}
          values={{}}
          onChange={vi.fn()}
        />
      </>
    )

    // 同一 query key：两个表单实例合并为一次请求。
    await waitFor(() => {
      expect(container.querySelectorAll('datalist')).toHaveLength(2)
    })
    expect(getConnections).toHaveBeenCalledTimes(1)
    const ids = [...container.querySelectorAll('datalist')].map((d) => d.id)
    expect(new Set(ids).size).toBe(2)
  })

  it('degrades to a plain text field when the connections API fails', async () => {
    vi.mocked(getConnections).mockRejectedValue(new Error('HTTP 403'))
    const { container } = renderForm(
      <SchemaConfigForm
        schema={connectionSchema}
        values={{}}
        onChange={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(getConnections).toHaveBeenCalled()
    })
    expect(container.querySelector('datalist')).toBeNull()
  })
})
