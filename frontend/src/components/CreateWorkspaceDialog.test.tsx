import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CreateWorkspaceDialog from './CreateWorkspaceDialog'

const mutateAsync = vi.fn()

vi.mock('../hooks/useWorkspaceMutations', () => ({
  useCreateWorkspace: () => ({ mutateAsync }),
}))

describe('CreateWorkspaceDialog', () => {
  beforeEach(() => {
    mutateAsync.mockReset()
    mutateAsync.mockResolvedValue({ id: 'my_ws' })
  })

  it('creates a blank-canvas workspace by default (no workflow picker)', async () => {
    const user = userEvent.setup()
    render(<CreateWorkspaceDialog open onClose={() => {}} />)
    await user.type(screen.getByRole('textbox'), 'My WS')

    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        name: 'My WS',
        workflowMode: 'blank',
      })
    )
  })

  it('creates a demo-mode workspace when the sample template checkbox is checked', async () => {
    const user = userEvent.setup()
    render(<CreateWorkspaceDialog open onClose={() => {}} />)
    await user.type(screen.getByRole('textbox'), 'My WS')

    await user.click(
      screen.getByLabelText('从示例模板初始化（教学视频脚本与题目生成）')
    )
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        name: 'My WS',
        workflowMode: 'demo',
      })
    )
  })
})
