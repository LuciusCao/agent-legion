import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
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

const yaml = `key: demo
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
      definitionYaml={yaml}
      setDefinitionYaml={() => {}}
      {...overrides}
    />
  )
}

describe('WorkflowNodeConfigSchemaSection', () => {
  it('lists config_schema properties with type and default', () => {
    renderSection()

    expect(
      screen.getByText(/bank_version（string，默认 v1）/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/dry_run（boolean，默认 false）/)
    ).toBeInTheDocument()
    expect(
      screen.getByRole('checkbox', { name: '运行开关 bank_version' })
    ).not.toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: '运行开关 dry_run' })
    ).not.toBeChecked()
  })

  it('checks properties already declared runtime_mutable', () => {
    renderSection({
      definitionYaml: yaml.replace(
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
    const next = setDefinitionYaml.mock.calls[0][0] as string
    expect(next).toContain('runtime_mutable: true')
    // 其余 schema 内容保持不变。
    expect(next).toContain('bank_version:')
    expect(next).toContain('default: v1')
  })

  it('removes the runtime_mutable key on uncheck instead of writing false', () => {
    const yamlMutable = yaml.replace(
      '        dry_run:\n          type: boolean',
      '        dry_run:\n          type: boolean\n          runtime_mutable: true'
    )
    const setDefinitionYaml = vi.fn()
    renderSection({ definitionYaml: yamlMutable, setDefinitionYaml })

    fireEvent.click(screen.getByRole('checkbox', { name: '运行开关 dry_run' }))

    const next = setDefinitionYaml.mock.calls[0][0] as string
    expect(next).not.toContain('runtime_mutable')
    expect(next).toContain('dry_run:')
  })

  it('renders an empty hint when the node declares no config_schema', () => {
    renderSection({
      definitionYaml: `key: demo
nodes:
  generate:
    capability: generate_questions
`,
    })

    expect(screen.getByText(/未声明 config_schema/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('locks editing in readOnly mode and explains why', () => {
    renderSection({ readOnly: true })

    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.getByText(/bank_version（string/)).toBeInTheDocument()
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

    expect(
      screen.getByText(/bank_version（string，默认 v1）/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('checkbox', { name: '运行开关 dry_run' })
    ).not.toBeInTheDocument()
  })

  it('points agent-backed nodes to the Agent definition instead of editing', () => {
    renderSection({ agentBacked: true })

    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(
      screen.getByText(/生效的配置 Schema 以 Agent 定义为准/)
    ).toBeInTheDocument()
  })
})
