import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { WorkflowMetadataEditor } from './WorkflowMetadataEditor'
import type { WorkflowDefinitionRecord } from '../../../types'

const workflow: WorkflowDefinitionRecord = {
  key: 'demo',
  label: 'Demo',
  intake: { modes: [] },
  nodes: [],
  edges: [],
}

const yaml = 'key: demo\nlabel: Demo\n'

describe('WorkflowMetadataEditor', () => {
  it('updates yaml when workflow label changes', () => {
    const onChange = vi.fn()
    render(
      <WorkflowMetadataEditor
        workflow={workflow}
        definitionYaml={yaml}
        onDefinitionYamlChange={onChange}
      />
    )

    fireEvent.change(screen.getByLabelText('Workflow 名称'), {
      target: { value: 'Demo v2' },
    })

    expect(onChange).toHaveBeenLastCalledWith(
      expect.stringContaining('label: Demo v2')
    )
  })

  it('derives input value from definitionYaml draft', () => {
    const { rerender } = render(
      <WorkflowMetadataEditor
        workflow={workflow}
        definitionYaml={yaml}
        onDefinitionYamlChange={vi.fn()}
      />
    )
    expect(screen.getByLabelText('Workflow 名称')).toHaveValue('Demo')

    rerender(
      <WorkflowMetadataEditor
        workflow={workflow}
        definitionYaml={`key: demo\nlabel: Demo v2\n`}
        onDefinitionYamlChange={vi.fn()}
      />
    )
    expect(screen.getByLabelText('Workflow 名称')).toHaveValue('Demo v2')
  })
})
