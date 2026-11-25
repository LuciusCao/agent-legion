import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { fetchRunDetail } from '../../api/sharedResources'
import {
  RunSharedResourceUsage,
  SharedResourceUsageList,
} from './SharedResourceUsage'

vi.mock('../../api/sharedResources', () => ({ fetchRunDetail: vi.fn() }))

const frozen = {
  resource_id: 'resource-1',
  name: '通用审核规则',
  version: 2,
  manifest_hash: 'b'.repeat(64),
  source: { type: 'material', id: 'm1', workspace_id: 'ws1', materials: [] },
  files: [],
}

describe('shared resource frozen usage', () => {
  beforeEach(() => vi.clearAllMocks())

  it('job 只展示本次冻结版本，不展示技术 hash', () => {
    render(<SharedResourceUsageList resources={[frozen]} />)
    expect(screen.getByText('通用审核规则 v2')).toBeInTheDocument()
    expect(screen.queryByText(frozen.manifest_hash)).not.toBeInTheDocument()
    expect(screen.getByText(/之后切换资源版本不会改变它/)).toBeInTheDocument()
  })

  it('run 展开详情时读取真实 run 记录', async () => {
    const user = userEvent.setup()
    vi.mocked(fetchRunDetail).mockResolvedValue({
      run: { shared_resources: [frozen] },
    } as never)
    render(
      <TestQueryProvider>
        <RunSharedResourceUsage workspaceId="ws1" runId="run-1" />
      </TestQueryProvider>
    )
    await user.click(screen.getByText('本次使用版本'))
    expect(await screen.findByText('通用审核规则 v2')).toBeInTheDocument()
    expect(screen.getAllByText('本次使用版本')).toHaveLength(1)
    expect(fetchRunDetail).toHaveBeenCalledWith(
      'ws1',
      'run-1',
      expect.any(AbortSignal)
    )
  })
})
