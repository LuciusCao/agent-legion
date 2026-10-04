import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApiAccessSection } from './ApiAccessSection'
import { listWorkspaceApiTokens } from '../../api'
import { TestQueryProvider } from '../../testing/testQueryClient'
import endpointCatalog from './apiAccessEndpoints.json'

vi.mock('../../api', () => ({
  createWorkspaceApiToken: vi.fn(),
  listWorkspaceApiTokens: vi.fn(),
  revokeWorkspaceApiToken: vi.fn(),
}))

const mockList = vi.mocked(listWorkspaceApiTokens)
const WORKSPACE_ID = 'demo_video_workflow'
const writeText = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  writeText.mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  mockList.mockResolvedValue({
    tokens: [],
    rate_limit: { requests_per_minute: 30, burst: 5 },
  })
})

function renderSection() {
  return render(
    <TestQueryProvider>
      <ApiAccessSection workspaceId={WORKSPACE_ID} />
    </TestQueryProvider>
  )
}

describe('ApiAccessSection', () => {
  it('shows the access parameters with the effective rate limit', async () => {
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('api-rate-limit').textContent).toContain(
        '每分钟补充 30 次请求，突发容量 5 次'
      )
    })
    expect(screen.getAllByText(WORKSPACE_ID).length).toBeGreaterThan(0)
    expect(screen.getAllByText(window.location.origin).length).toBeGreaterThan(
      0
    )
    // token 管理与接入信息同居一个 section：列表只请求一次（共用 query）。
    expect(mockList).toHaveBeenCalledTimes(1)
    expect(screen.getByText('还没有 API Token')).toBeTruthy()
  })

  it('lists exactly the documented endpoint catalog', () => {
    renderSection()

    const rows = screen.getAllByTestId('api-access-endpoint')
    expect(rows).toHaveLength(endpointCatalog.endpoints.length)
    expect(rows[0].textContent).toContain('POST')
    expect(rows[0].textContent).toContain('/runs')
    expect(screen.getByText(`/api/workspaces/${WORKSPACE_ID}`)).toBeTruthy()
  })

  it('fills examples with this workspace and switches between curl and Python', () => {
    renderSection()

    const example = () => screen.getByTestId('api-access-example').textContent
    expect(example()).toContain(`WORKSPACE_ID="${WORKSPACE_ID}"`)
    expect(example()).toContain(`API_BASE="${window.location.origin}"`)
    expect(example()).toContain('curl -sS -X POST')

    fireEvent.click(screen.getByRole('tab', { name: 'Python' }))
    expect(example()).toContain('import requests')
    expect(example()).toContain(`WORKSPACE_ID = "${WORKSPACE_ID}"`)
  })

  it('copies the access summary and the active example', async () => {
    renderSection()

    await waitFor(() => {
      expect(screen.getByTestId('api-rate-limit').textContent).toContain('30')
    })
    fireEvent.click(screen.getByRole('button', { name: '复制接入信息' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const summary = writeText.mock.calls[0][0] as string
    expect(summary).toContain(`Workspace ID: ${WORKSPACE_ID}`)
    expect(summary).toContain(`API Base: ${window.location.origin}`)
    expect(summary).toContain('Authorization: Bearer')
    expect(summary).toContain('每分钟补充 30 次请求')

    fireEvent.click(screen.getByRole('button', { name: '复制 curl 示例' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2))
    expect(writeText.mock.calls[1][0]).toBe(
      screen.getByTestId('api-access-example').textContent
    )
  })

  it('reports a clipboard failure instead of claiming success', async () => {
    writeText.mockRejectedValue(new Error('denied'))
    renderSection()

    fireEvent.click(screen.getByRole('button', { name: '复制 Workspace ID' }))
    await waitFor(() => {
      expect(screen.getByText('复制失败，请手动选择')).toBeTruthy()
    })
  })
})
