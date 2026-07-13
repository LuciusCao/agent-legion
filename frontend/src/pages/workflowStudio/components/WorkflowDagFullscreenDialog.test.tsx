import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkflowDagFullscreenButton } from './WorkflowDagFullscreenButton'
import { WorkflowDagFullscreenDialog } from './WorkflowDagFullscreenDialog'
import type { WorkflowDefinitionRecord } from '../../../types'

const workflow: WorkflowDefinitionRecord = {
  key: 'demo',
  label: 'Demo',
  intake: { modes: [] },
  nodes: [
    {
      key: 'a',
      label: 'A',
      capability: 'cap_a',
      after: [],
      inputs: [],
      outputs: [],
    },
  ],
  edges: [],
}

const executorCatalog = [
  {
    id: 'local-default',
    kind: 'local' as const,
    global_capacity: 4,
    capabilities: ['cap_a'],
  },
]

describe('WorkflowDagFullscreenDialog', () => {
  it('opens fullscreen dialog when button is clicked', async () => {
    const onOpen = vi.fn()
    render(<WorkflowDagFullscreenButton onClick={onOpen} />)

    await userEvent.click(
      screen.getByRole('button', { name: 'open fullscreen DAG' })
    )

    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('labels fullscreen dialog and close button for focus mode', () => {
    render(
      <WorkflowDagFullscreenDialog
        open
        workflow={workflow}
        executorCatalog={executorCatalog}
        selectedNode={null}
        onSelectedNodeChange={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(
      screen.getByRole('dialog', { name: 'Workflow DAG focus mode' })
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'close fullscreen DAG' })
    ).toBeInTheDocument()
    expect(screen.getByText('local')).toBeInTheDocument()
  })

  it('closes dialog and preserves selected node state', async () => {
    const onSelectedNodeChange = vi.fn()
    const onClose = vi.fn()

    render(
      <WorkflowDagFullscreenDialog
        open
        workflow={workflow}
        executorCatalog={executorCatalog}
        selectedNode="a"
        onSelectedNodeChange={onSelectedNodeChange}
        onClose={onClose}
      />
    )

    expect(
      screen.getByRole('button', { name: 'close fullscreen DAG' })
    ).toBeInTheDocument()

    await userEvent.click(
      screen.getByRole('button', { name: 'close fullscreen DAG' })
    )

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('renders nothing to click when closed', () => {
    const { container } = render(
      <WorkflowDagFullscreenDialog
        open={false}
        workflow={workflow}
        executorCatalog={executorCatalog}
        selectedNode={null}
        onSelectedNodeChange={vi.fn()}
        onClose={vi.fn()}
      />
    )

    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument()
  })
})
