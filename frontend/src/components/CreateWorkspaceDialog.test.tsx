import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CreateWorkspaceDialog from './CreateWorkspaceDialog'

const mutateAsync = vi.fn()

vi.mock('../hooks/useWorkspaceMutations', () => ({
  useCreateWorkspace: () => ({ mutateAsync }),
}))

vi.mock('../api', () => ({
  fetchWorkflows: vi.fn().mockResolvedValue({
    workflows: [{ key: 'demo_workflow', label: 'Demo Workflow' }],
  }),
}))

async function fillAndOpenDialog() {
  const user = userEvent.setup()
  render(<CreateWorkspaceDialog open onClose={() => {}} />)
  await user.type(screen.getByRole('textbox'), 'My WS')
  await user.click(screen.getByRole('combobox', { name: '工作流' }))
  await user.click(await screen.findByRole('option', { name: 'Demo Workflow' }))
  return user
}

describe('CreateWorkspaceDialog', () => {
  beforeEach(() => {
    mutateAsync.mockReset()
    mutateAsync.mockResolvedValue({ id: 'my_ws' })
  })

  it('creates a demo-mode workspace by default', async () => {
    const user = await fillAndOpenDialog()

    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        name: 'My WS',
        workflowKey: 'demo_workflow',
        workflowMode: 'demo',
      })
    )
  })

  it('creates a blank-mode workspace when the blank checkbox is checked', async () => {
    const user = await fillAndOpenDialog()

    await user.click(screen.getByLabelText('空白（从零搭建）'))
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        name: 'My WS',
        workflowKey: 'demo_workflow',
        workflowMode: 'blank',
      })
    )
  })
})
