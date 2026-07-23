import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { PackageHistoryDialog } from './PackageHistoryDialog'
import * as api from '../api'
import * as download from '../lib/download'

vi.mock('../api')
vi.mock('../lib/download')

const WORKSPACE_ID = 'ws-1'

const samplePackage = {
  id: 1,
  name: '批次 1',
  path: '/data/packages/workspace-ws-1/a.zip',
  video_count: 2,
  size_bytes: 1024 * 1024,
  locked: 0,
  created_at: new Date().toISOString(),
  workspace_id: WORKSPACE_ID,
}

function renderDialog() {
  return render(
    <PackageHistoryDialog
      open={true}
      onClose={vi.fn()}
      workspaceId={WORKSPACE_ID}
    />
  )
}

describe('PackageHistoryDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    window.confirm = vi.fn(() => true)
  })

  it('renders empty state when no packages', async () => {
    vi.mocked(api.fetchWorkspacePackages).mockResolvedValue({ packages: [] })
    renderDialog()
    await waitFor(() => {
      expect(screen.getByText('暂无打包记录')).toBeInTheDocument()
    })
    expect(api.fetchWorkspacePackages).toHaveBeenCalledWith(WORKSPACE_ID)
  })

  it('renders package list with metadata', async () => {
    vi.mocked(api.fetchWorkspacePackages).mockResolvedValue({
      packages: [samplePackage],
    })
    renderDialog()
    await waitFor(() => {
      expect(screen.getByText('批次 1')).toBeInTheDocument()
    })
    expect(screen.getByText(/2个任务/)).toBeInTheDocument()
  })

  it('triggers workspace download when download button clicked', async () => {
    vi.mocked(api.fetchWorkspacePackages).mockResolvedValue({
      packages: [samplePackage],
    })
    renderDialog()
    await waitFor(() => screen.getByText('批次 1'))
    const btn = screen.getByTitle('下载')
    fireEvent.click(btn)
    expect(download.triggerDownload).toHaveBeenCalledWith(
      `/api/workspaces/${WORKSPACE_ID}/packages/a.zip`
    )
  })

  it('deletes workspace package and refreshes list', async () => {
    vi.mocked(api.fetchWorkspacePackages).mockResolvedValue({
      packages: [samplePackage],
    })
    vi.mocked(api.deleteWorkspacePackage).mockResolvedValue({ deleted: true })
    renderDialog()
    await waitFor(() => screen.getByText('批次 1'))
    const btn = screen.getByTitle('删除')
    fireEvent.click(btn)
    await waitFor(() => {
      expect(api.deleteWorkspacePackage).toHaveBeenCalledWith(WORKSPACE_ID, 1)
    })
  })
})
