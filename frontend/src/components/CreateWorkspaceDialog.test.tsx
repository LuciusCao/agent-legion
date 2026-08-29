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

  it('creates a workspace with the explicit id and name (v62 binding)', async () => {
    const user = userEvent.setup()
    render(<CreateWorkspaceDialog open onClose={() => {}} />)
    await user.type(
      screen.getByRole('textbox', { name: /Workspace ID/ }),
      'my_ws'
    )
    await user.type(
      screen.getByRole('textbox', { name: /Workspace 名称/ }),
      'My WS'
    )

    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({ id: 'my_ws', name: 'My WS' })
    )
  })

  it('blocks submission while the id does not match the v62 pattern', async () => {
    const user = userEvent.setup()
    render(<CreateWorkspaceDialog open onClose={() => {}} />)
    await user.type(
      screen.getByRole('textbox', { name: /Workspace ID/ }),
      'Bad ID'
    )
    await user.type(
      screen.getByRole('textbox', { name: /Workspace 名称/ }),
      'My WS'
    )

    expect(screen.getByRole('button', { name: '创建' })).toBeDisabled()
    expect(mutateAsync).not.toHaveBeenCalled()
  })
})
