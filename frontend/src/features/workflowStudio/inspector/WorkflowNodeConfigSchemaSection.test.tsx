import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import yaml from 'js-yaml'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeConfigSchemaSection } from './WorkflowNodeConfigSchemaSection'

const node: WorkflowNodeRecord = {
  key: 'generate',
  label: 'Generate',
  capability: 'generate_questions',
  after: [],
  inputs: [],
  outputs: [],
}

const yamlText = `key: demo
nodes:
  generate:
    label: Generate
    capability: generate_questions
    config_schema:
      type: object
      properties:
        bank_version:
          type: string
          default: v1
        dry_run:
          type: boolean
          default: false
`

function renderSection(
  overrides?: Partial<Parameters<typeof WorkflowNodeConfigSchemaSection>[0]>
) {
  return render(
    <WorkflowNodeConfigSchemaSection
      node={node}
      definitionYaml={yamlText}
      setDefinitionYaml={() => {}}
      {...overrides}
    />
  )
}

/** 从 setDefinitionYaml 收到的下一版 YAML 里读回节点的 config_schema。 */
function patchedSchema(call: unknown): Record<string, unknown> {
  const next = yaml.load(String(call)) as {
    nodes?: Record<string, { config_schema?: Record<string, unknown> }>
  }
  return next.nodes?.generate?.config_schema ?? {}
}

describe('WorkflowNodeConfigSchemaSection (#418 structured editor)', () => {
  it('renders editable rows for each declared property', () => {
    renderSection()

    expect(screen.getByLabelText('属性名 bank_version')).toHaveValue(
      'bank_version'
    )
    expect(screen.getByLabelText('类型 bank_version')).toHaveValue('string')
    expect(screen.getByLabelText('默认值 bank_version')).toHaveValue('v1')
    expect(screen.getByLabelText('类型 dry_run')).toHaveValue('boolean')
    expect(
      screen.queryByRole('checkbox', { name: '运行开关 dry_run' })
    ).not.toBeChecked()
  })

  it('checks properties already declared runtime_mutable', () => {
    renderSection({
      definitionYaml: yamlText.replace(
        '        dry_run:\n          type: boolean',
        '        dry_run:\n          type: boolean\n          runtime_mutable: true'
      ),
    })

    expect(
      screen.getByRole('checkbox', { name: '运行开关 dry_run' })
    ).toBeChecked()
  })

  it('patches runtime_mutable: true into the draft yaml on check', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.click(screen.getByRole('checkbox', { name: '运行开关 dry_run' }))

    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    expect(schema).toMatchObject({
      properties: { dry_run: { runtime_mutable: true } },
    })
    // 其余 schema 内容保持不变。
    expect(schema).toMatchObject({
      properties: { bank_version: { type: 'string', default: 'v1' } },
    })
  })

  it('removes the runtime_mutable key on uncheck instead of writing false', () => {
    const yamlMutable = yamlText.replace(
      '        dry_run:\n          type: boolean',
      '        dry_run:\n          type: boolean\n          runtime_mutable: true'
    )
    const setDefinitionYaml = vi.fn()
    renderSection({ definitionYaml: yamlMutable, setDefinitionYaml })

    fireEvent.click(screen.getByRole('checkbox', { name: '运行开关 dry_run' }))

    const schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    expect(
      (schema.properties as Record<string, Record<string, unknown>>).dry_run
    ).not.toHaveProperty('runtime_mutable')
  })

  it('adds a new property with the given name and defaults to string type', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.change(screen.getByLabelText('新增属性名'), {
      target: { value: 'page_size' },
    })
    fireEvent.click(screen.getByRole('button', { name: '新增' }))

    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    const schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    expect(schema).toMatchObject({
      properties: {
        page_size: { type: 'string' },
        bank_version: { type: 'string', default: 'v1' },
      },
    })
  })

  it('rejects duplicate, empty, and platform-reserved property names', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    // 重名。
    fireEvent.change(screen.getByLabelText('新增属性名'), {
      target: { value: 'dry_run' },
    })
    fireEvent.click(screen.getByRole('button', { name: '新增' }))
    expect(screen.getByRole('alert')).toHaveTextContent('属性 dry_run 已存在')
    expect(setDefinitionYaml).not.toHaveBeenCalled()

    // 平台保留执行键。
    fireEvent.change(screen.getByLabelText('新增属性名'), {
      target: { value: 'timeout_seconds' },
    })
    fireEvent.click(screen.getByRole('button', { name: '新增' }))
    expect(screen.getByRole('alert')).toHaveTextContent('平台保留执行键')
    expect(setDefinitionYaml).not.toHaveBeenCalled()

    // 空名。
    fireEvent.change(screen.getByLabelText('新增属性名'), {
      target: { value: '   ' },
    })
    fireEvent.click(screen.getByRole('button', { name: '新增' }))
    expect(screen.getByRole('alert')).toHaveTextContent('属性名不能为空')
    expect(setDefinitionYaml).not.toHaveBeenCalled()
  })

  it('renames a property, keeping its declaration', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.change(screen.getByLabelText('属性名 dry_run'), {
      target: { value: 'preview_mode' },
    })
    fireEvent.blur(screen.getByLabelText('属性名 dry_run'))

    const schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    expect(schema).toMatchObject({
      properties: {
        preview_mode: { type: 'boolean', default: false },
      },
    })
    expect(
      (schema.properties as Record<string, unknown>).dry_run
    ).toBeUndefined()
  })

  it('changes a property type and drops the now-incompatible default', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.change(screen.getByLabelText('类型 bank_version'), {
      target: { value: 'integer' },
    })

    const schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    // string 默认 v1 对 integer 非法（loader 会拒），改类型时清掉。
    expect(schema).toMatchObject({
      properties: { bank_version: { type: 'integer' } },
    })
    expect(
      (schema.properties as Record<string, Record<string, unknown>>)
        .bank_version
    ).not.toHaveProperty('default')
  })

  it('updates description and default through the row editors', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.change(screen.getByLabelText('描述 dry_run'), {
      target: { value: '试运行开关' },
    })
    fireEvent.blur(screen.getByLabelText('描述 dry_run'))

    let schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    expect(schema).toMatchObject({
      properties: { dry_run: { description: '试运行开关' } },
    })

    fireEvent.change(screen.getByLabelText('默认值 dry_run'), {
      target: { value: 'true' },
    })
    schema = patchedSchema(setDefinitionYaml.mock.calls[1][0])
    expect(schema).toMatchObject({
      properties: { dry_run: { default: true } },
    })
  })

  it('removes a single property and keeps the rest of the schema', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.click(screen.getByRole('button', { name: '删除属性 dry_run' }))

    const schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    expect(
      (schema.properties as Record<string, unknown>).dry_run
    ).toBeUndefined()
    expect(schema).toMatchObject({
      properties: { bank_version: { type: 'string' } },
    })
  })

  it('drops the whole config_schema block when the last property is removed', () => {
    const singlePropYaml = `key: demo
nodes:
  generate:
    capability: generate_questions
    config_schema:
      type: object
      properties:
        dry_run:
          type: boolean
`
    const setDefinitionYaml = vi.fn()
    renderSection({ definitionYaml: singlePropYaml, setDefinitionYaml })

    fireEvent.click(screen.getByRole('button', { name: '删除属性 dry_run' }))

    const next = yaml.load(String(setDefinitionYaml.mock.calls[0][0])) as {
      nodes?: Record<string, Record<string, unknown>>
    }
    expect(next.nodes?.generate).not.toHaveProperty('config_schema')
  })

  it('creates the schema skeleton when adding the first property', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({
      definitionYaml: `key: demo
nodes:
  generate:
    capability: generate_questions
`,
      setDefinitionYaml,
    })

    expect(screen.getByText(/未声明 config_schema/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('新增属性名'), {
      target: { value: 'dry_run' },
    })
    fireEvent.click(screen.getByRole('button', { name: '新增' }))

    const schema = patchedSchema(setDefinitionYaml.mock.calls[0][0])
    expect(schema).toMatchObject({
      type: 'object',
      properties: { dry_run: { type: 'string' } },
    })
  })

  it('removes the whole schema via the section-level button', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.click(screen.getByRole('button', { name: '删除整段 Schema' }))

    const next = yaml.load(String(setDefinitionYaml.mock.calls[0][0])) as {
      nodes?: Record<string, Record<string, unknown>>
    }
    expect(next.nodes?.generate).not.toHaveProperty('config_schema')
  })

  it('locks all editors in readOnly mode and explains why', () => {
    renderSection({ readOnly: true })

    expect(screen.getByLabelText('属性名 bank_version')).toBeDisabled()
    expect(screen.getByLabelText('类型 bank_version')).toBeDisabled()
    expect(screen.getByLabelText('描述 bank_version')).toBeDisabled()
    expect(screen.getByLabelText('默认值 bank_version')).toBeDisabled()
    expect(
      screen.getByRole('checkbox', { name: '运行开关 bank_version' })
    ).toBeDisabled()
    // 新增/删除入口整体不渲染。
    expect(screen.queryByLabelText('新增属性名')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '删除属性 bank_version' })
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/历史版本查看模式下配置 Schema 不可编辑/)
    ).toBeInTheDocument()
  })

  it('skips null properties from mid-edit YAML instead of crashing', () => {
    renderSection({
      definitionYaml: `key: demo
nodes:
  generate:
    capability: generate_questions
    config_schema:
      type: object
      properties:
        bank_version:
          type: string
          default: v1
        dry_run:
`,
    })

    expect(screen.getByLabelText('属性名 bank_version')).toHaveValue(
      'bank_version'
    )
    expect(screen.queryByLabelText('属性名 dry_run')).not.toBeInTheDocument()
  })

  it('does not render a node-owned schema section for agent nodes (#406)', () => {
    renderSection({ node: { ...node, node_type: 'agent' } })

    expect(
      screen.queryByLabelText('配置 Schema generate')
    ).not.toBeInTheDocument()
  })
})
