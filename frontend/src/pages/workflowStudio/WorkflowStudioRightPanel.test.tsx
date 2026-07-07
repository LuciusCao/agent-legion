import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'

const workflow = {
  key: 'video_knowledge',
  label: '知识视频 DAG',
  intake: { modes: [] },
  nodes: [],
  edges: [],
}

describe('WorkflowStudioRightPanel', () => {
  it('switches between overview, changes, yaml, and validation modes', () => {
    const { rerender } = render(
      <WorkflowStudioRightPanel
        workflow={workflow}
        selectedNodeKey={null}
        readOnly={false}
        definitionYaml="key: video_knowledge\n"
        setDefinitionYaml={vi.fn()}
        compareSummary={null}
        compareState="idle"
        compareErrors={null}
        validationMessage=""
        validationErrors={[]}
        onSelectNode={vi.fn()}
      />
    )

    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
      'aria-selected',
      'true'
    )

    rerender(
      <WorkflowStudioRightPanel
        workflow={workflow}
        selectedNodeKey={null}
        readOnly={false}
        definitionYaml="key: video_knowledge\n"
        setDefinitionYaml={vi.fn()}
        compareSummary={null}
        compareState="idle"
        compareErrors={null}
        validationMessage="校验通过"
        validationErrors={[]}
        onSelectNode={vi.fn()}
      />
    )

    expect(screen.getByRole('tab', { name: 'Validation' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByText('校验通过')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'YAML' }))
    expect(screen.getByLabelText('高级 YAML 编辑器')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Validation' }))
    expect(screen.getByText('校验通过')).toBeInTheDocument()
  })
})
