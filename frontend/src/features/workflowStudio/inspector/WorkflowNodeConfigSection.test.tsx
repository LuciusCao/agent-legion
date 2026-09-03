import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import yaml from 'js-yaml'
import type { ConfigSchema, WorkflowNodeRecord } from '../../../types'
import { useSettingStore } from '../../../stores/settingStore'
import { TestQueryProvider } from '../../../testing/testQueryClient'
import { WorkflowNodeConfigSection } from './WorkflowNodeConfigSection'

const node: WorkflowNodeRecord = {
  key: 'generate',
  label: 'Generate',
  capability: 'generate_questions',
  after: [],
  inputs: [],
  outputs: [],
  node_type: 'code',
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
        page_size:
          type: integer
          default: 50
    config:
      page_size: 20
`

const liveSchema: ConfigSchema = {
  type: 'object',
  properties: {
    page_size: { type: 'integer', default: 100 },
    dry_run: { type: 'boolean', default: false, runtime_mutable: true },
  },
}

function renderSection(
  overrides?: Partial<Parameters<typeof WorkflowNodeConfigSection>[0]>
) {
  return render(
    <TestQueryProvider>
      <WorkflowNodeConfigSection
        node={node}
        definitionYaml={yamlText}
        setDefinitionYaml={() => {}}
        {...overrides}
      />
    </TestQueryProvider>
  )
}

function patchedConfig(call: unknown): Record<string, unknown> {
  const next = yaml.load(String(call)) as {
    nodes?: Record<string, { config?: Record<string, unknown> }>
  }
  return next.nodes?.generate?.config ?? {}
}

describe('WorkflowNodeConfigSection dual-channel (#418)', () => {
  beforeEach(() => {
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: {
        entityType: 'question',
        previewHidden: [],
        nodeConfig: { generate: { page_size: 30 } },
        nodeConfigSchemas: { generate: liveSchema },
      },
    })
  })

  it('renders the revision-scoped value form from the draft config_schema', () => {
    renderSection()

    // 版本值表单按草稿 schema 的属性生成，初值来自草稿 config。
    expect(screen.getByLabelText('版本值 bank_version')).toHaveValue('')
    expect(screen.getByLabelText('版本值 page_size')).toHaveValue('20')
    // boolean 属性用下拉（Schema 默认 / true / false）。
    const dryRun = screen.getByLabelText('版本值 dry_run') as HTMLSelectElement
    expect(dryRun.value).toBe('')
    expect(screen.getByText(/版本值：写入 workflow 定义/)).toBeInTheDocument()
  })

  it('writes a value change into the draft YAML (revision channel)', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    fireEvent.change(screen.getByLabelText('版本值 page_size'), {
      target: { value: '33' },
    })

    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    expect(patchedConfig(setDefinitionYaml.mock.calls[0][0])).toEqual({
      page_size: 33,
    })
  })

  it('clears a boolean override back to the schema default', () => {
    const booleanYaml = yamlText.replace(
      '    config:\n      page_size: 20',
      '    config:\n      page_size: 20\n      dry_run: true'
    )
    const setDefinitionYaml = vi.fn()
    renderSection({
      definitionYaml: booleanYaml,
      setDefinitionYaml,
    })

    fireEvent.change(screen.getByLabelText('版本值 dry_run'), {
      target: { value: '' },
    })

    expect(patchedConfig(setDefinitionYaml.mock.calls[0][0])).toEqual({
      page_size: 20,
    })
  })

  it('removes the config block when the last key is cleared', () => {
    const singleYaml = `key: demo
nodes:
  generate:
    capability: generate_questions
    config_schema:
      type: object
      properties:
        page_size:
          type: integer
          default: 50
    config:
      page_size: 20
`
    const setDefinitionYaml = vi.fn()
    renderSection({ definitionYaml: singleYaml, setDefinitionYaml })

    fireEvent.change(screen.getByLabelText('版本值 page_size'), {
      target: { value: '' },
    })

    const next = yaml.load(String(setDefinitionYaml.mock.calls[0][0])) as {
      nodes?: Record<string, Record<string, unknown>>
    }
    expect(next.nodes?.generate).not.toHaveProperty('config')
  })

  it('falls back to the schema default when a numeric field is cleared or invalid', () => {
    const setDefinitionYaml = vi.fn()
    renderSection({ setDefinitionYaml })

    // 非数字输入解析为「未填」：键从 config 删除，回退 Schema 默认值。
    fireEvent.change(screen.getByLabelText('版本值 page_size'), {
      target: { value: 'abc' },
    })

    expect(setDefinitionYaml).toHaveBeenCalledTimes(1)
    expect(patchedConfig(setDefinitionYaml.mock.calls[0][0])).toEqual({})
  })

  it('shows the runtime override card labeled as immediately effective', () => {
    renderSection()

    expect(
      screen.getByText(/运行时覆盖：立即保存到 workspace 设置/)
    ).toBeInTheDocument()
    // NodeConfigCard（live 通道）从 settingStore 的 live schema 生成。
    expect(screen.getByRole('spinbutton', { name: 'page_size' })).toHaveValue(
      30
    )
    // runtime_mutable 键带徽标。
    expect(screen.getByText('dry_run · 运行开关')).toBeInTheDocument()
  })

  it('explains the two channels when no live schema exists', () => {
    useSettingStore.setState({
      workspaceId: 'ws1',
      settings: {
        entityType: 'question',
        previewHidden: [],
      },
    })
    renderSection()

    // 无 live schema：运行时覆盖通道整体不渲染，只留版本值通道。
    expect(screen.getByLabelText('版本值 page_size')).toBeInTheDocument()
    expect(screen.queryByText(/运行时覆盖/)).not.toBeInTheDocument()
  })

  it('renders only the runtime override channel for agent nodes', () => {
    const agentNode = { ...node, node_type: 'agent' as const }
    renderSection({ node: agentNode })

    // agent 节点的 schema 归 Agent Definition：无版本值表单。
    expect(screen.queryByLabelText('版本值 page_size')).not.toBeInTheDocument()
    expect(
      screen.queryByText(/未声明 config_schema 的节点没有可配置参数/)
    ).not.toBeInTheDocument()
    // 运行时覆盖通道保留（live schema 来自 Agent Definition）。
    expect(screen.getByRole('spinbutton', { name: 'page_size' })).toHaveValue(
      30
    )
  })

  it('locks both channels in readOnly mode', () => {
    renderSection({ readOnly: true })

    expect(screen.getByLabelText('版本值 page_size')).toBeDisabled()
    expect(
      screen.getByText(/历史版本查看模式下运行时覆盖不可编辑/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '保存' })
    ).not.toBeInTheDocument()
  })

  it('hints at the schema section when the code node declares no schema', () => {
    renderSection({
      definitionYaml: `key: demo
nodes:
  generate:
    capability: generate_questions
`,
    })

    expect(
      screen.getByText(/未声明 config_schema 的节点没有可配置参数/)
    ).toBeInTheDocument()
    expect(screen.queryByLabelText('版本值 page_size')).not.toBeInTheDocument()
  })

  it('does not crash on mid-edit invalid YAML (read-side defense)', () => {
    renderSection({
      definitionYaml: yamlText.replace(
        '    config:\n      page_size: 20',
        '    config: {page_size: 20'
      ),
    })

    // 读侧吞错：无版本值表单但不崩（live 通道仍渲染）。
    expect(screen.queryByLabelText('版本值 page_size')).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'page_size' })).toHaveValue(
      30
    )
  })
})
