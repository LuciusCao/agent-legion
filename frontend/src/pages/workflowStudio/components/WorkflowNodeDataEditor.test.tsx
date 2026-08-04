import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { WorkflowNodeRecord } from '../../../types'
import { WorkflowNodeDataEditor } from './WorkflowNodeDataEditor'

const node: WorkflowNodeRecord = {
  key: 'done',
  label: 'Done',
  capability: 'assemble',
  after: [],
  inputs: ['input.json'],
  outputs: ['output.json'],
  terminal: { outcome: 'uploadable' },
}
const yaml = `nodes:\n  done:\n    capability: assemble\n    inputs: [input.json]\n    outputs: [output.json]\n    terminal:\n      outcome: uploadable\n`

it('updates data contract fields in workflow yaml', () => {
  const setDefinitionYaml = vi.fn()
  render(
    <WorkflowNodeDataEditor
      node={node}
      definitionYaml={yaml}
      setDefinitionYaml={setDefinitionYaml}
    />
  )

  fireEvent.change(screen.getByLabelText('输入产物，每行一个'), {
    target: { value: 'a.json\nb.json' },
  })
  fireEvent.change(screen.getByLabelText('Terminal Outcome'), {
    target: { value: 'archived' },
  })

  expect(setDefinitionYaml).toHaveBeenCalledWith(
    expect.stringContaining('a.json')
  )
  expect(setDefinitionYaml).toHaveBeenCalledWith(
    expect.stringContaining('outcome: archived')
  )
})
