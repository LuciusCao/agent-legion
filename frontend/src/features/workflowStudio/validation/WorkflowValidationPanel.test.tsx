import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkflowValidationPanel } from './WorkflowValidationPanel'
import type { components } from '../../../generated/api'

type CompareError = components['schemas']['WorkflowDraftCompareError']

describe('WorkflowValidationPanel', () => {
  it('renders grouped string errors', () => {
    render(
      <WorkflowValidationPanel
        message="校验失败"
        errors={[
          "YAML parse error: could not find expected ':'",
          'Schema validation failed: nodes required',
          'missing executor binding for demo.node',
          'No active revision found',
        ]}
      />
    )

    expect(screen.getByText('校验失败')).toBeInTheDocument()
    expect(screen.getByText('YAML解析')).toBeInTheDocument()
    expect(screen.getByText('结构校验')).toBeInTheDocument()
    expect(screen.getByText('执行器绑定')).toBeInTheDocument()
    expect(screen.getByText('版本')).toBeInTheDocument()
  })

  it('renders grouped compare errors with node scope', async () => {
    const onSelectNode = vi.fn()
    const compareErrors: CompareError[] = [
      {
        category: 'yaml',
        message: "could not find expected ':'",
        line: 18,
        column: 7,
      },
      {
        category: 'schema',
        message: 'Missing capability',
        node_key: 'classify',
      },
    ]

    render(
      <WorkflowValidationPanel
        message=""
        errors={[]}
        compareErrors={compareErrors}
        onSelectNode={onSelectNode}
      />
    )

    expect(screen.getByText('YAML解析')).toBeInTheDocument()
    expect(screen.getByText('结构校验')).toBeInTheDocument()

    await userEvent.click(screen.getByText('节点: classify'))

    expect(onSelectNode).toHaveBeenCalledWith('classify')
  })

  it('renders edge scope for source and target', () => {
    const compareErrors: CompareError[] = [
      {
        category: 'structure',
        message: 'Edge target missing',
        source: 'classify',
        target: 'missing',
      },
    ]

    render(
      <WorkflowValidationPanel
        message=""
        errors={[]}
        compareErrors={compareErrors}
      />
    )

    expect(screen.getByText('classify → missing')).toBeInTheDocument()
  })

  it('returns null when there is no message or errors', () => {
    const { container } = render(
      <WorkflowValidationPanel message="" errors={[]} />
    )

    expect(container.firstChild).toBeNull()
  })
})
