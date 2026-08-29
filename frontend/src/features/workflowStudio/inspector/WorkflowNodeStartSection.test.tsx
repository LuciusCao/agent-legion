import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WorkflowNodeStartSection } from './WorkflowNodeStartSection'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'

function makeDetails(types?: string[]): SelectedWorkflowNodeDetails {
  return {
    node: {
      key: '_start',
      label: '入口',
      capability: '',
      node_type: 'start',
      accepted_item_types: types,
      after: [],
      inputs: [],
      outputs: [],
    } as never,
    incoming: [],
    outgoing: [],
  }
}

describe('WorkflowNodeStartSection readOnly view', () => {
  it('shows user-facing labels instead of raw enum values', () => {
    render(
      <WorkflowNodeStartSection
        details={makeDetails(['material', 'ref'])}
        definitionYaml=""
        setDefinitionYaml={() => {}}
        readOnly
      />
    )

    expect(
      screen.getByText('接受条目类型：上传文件、外部平台内容')
    ).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('shows the bundle label when the contract includes folders', () => {
    render(
      <WorkflowNodeStartSection
        details={makeDetails(['material', 'ref', 'bundle'])}
        definitionYaml=""
        setDefinitionYaml={() => {}}
        readOnly
      />
    )

    expect(
      screen.getByText('接受条目类型：上传文件、外部平台内容、整个文件夹')
    ).toBeInTheDocument()
  })

  it('keeps the undeclared placeholder when the contract is empty', () => {
    render(
      <WorkflowNodeStartSection
        details={makeDetails(undefined)}
        definitionYaml=""
        setDefinitionYaml={() => {}}
        readOnly
      />
    )

    expect(screen.getByText('接受条目类型：（未声明）')).toBeInTheDocument()
  })
})
