import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { PackageHistoryDialog } from './PackageHistoryDialog'
import * as api from '../api'
import * as download from '../lib/download'
import { usePackageStore } from '../stores/packageStore'

vi.mock('../api')
vi.mock('../lib/download')

describe('PackageHistoryDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    window.confirm = vi.fn(() => true)
    usePackageStore.setState({ packages: [], loading: false })
  })

  it('renders empty state when no packages', async () => {
    vi.mocked(api.fetchPackages).mockResolvedValue({ packages: [] })
    render(<PackageHistoryDialog open={true} onClose={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('暂无打包记录')).toBeInTheDocument()
    })
  })

  it('renders package list with metadata', async () => {
    vi.mocked(api.fetchPackages).mockResolvedValue({
      packages: [
        {
          id: 1,
          name: '批次 1',
          path: '/data/packages/a.zip',
          video_count: 10,
          size_bytes: 1024 * 1024,
          created_at: new Date().toISOString(),
        },
      ],
    })
    render(<PackageHistoryDialog open={true} onClose={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText('批次 1')).toBeInTheDocument()
    })
    expect(screen.getByText(/10个视频/)).toBeInTheDocument()
  })

  it('triggers download when download button clicked', async () => {
    vi.mocked(api.fetchPackages).mockResolvedValue({
      packages: [
        {
          id: 1,
          name: '批次 1',
          path: '/data/packages/a.zip',
          video_count: 1,
          size_bytes: 100,
          created_at: new Date().toISOString(),
        },
      ],
    })
    render(<PackageHistoryDialog open={true} onClose={vi.fn()} />)
    await waitFor(() => screen.getByText('批次 1'))
    const btn = screen.getByTitle('下载')
    fireEvent.click(btn)
    expect(download.triggerDownload).toHaveBeenCalledWith('/api/packages/a.zip')
  })

  it('deletes package and refreshes list', async () => {
    vi.mocked(api.fetchPackages).mockResolvedValue({
      packages: [
        {
          id: 1,
          name: '批次 1',
          path: '/data/packages/a.zip',
          video_count: 1,
          size_bytes: 100,
          created_at: new Date().toISOString(),
        },
      ],
    })
    vi.mocked(api.deletePackage).mockResolvedValue({ deleted: true })
    render(<PackageHistoryDialog open={true} onClose={vi.fn()} />)
    await waitFor(() => screen.getByText('批次 1'))
    const btn = screen.getByTitle('删除')
    fireEvent.click(btn)
    await waitFor(() => {
      expect(api.deletePackage).toHaveBeenCalledWith(1)
    })
  })
})
