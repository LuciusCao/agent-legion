import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeSharedResourceVersion } from '../../testing/sharedResourceFixtures'
import { getSharedResourceVersion } from '../../api/sharedResources'
import { ResourceVersionDiff } from './ResourceVersionDiff'

vi.mock('../../api/sharedResources', () => ({
  getSharedResourceVersion: vi.fn(),
}))

describe('ResourceVersionDiff', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getSharedResourceVersion).mockImplementation(
      (_ws, _resource, version) =>
        Promise.resolve({
          version: makeSharedResourceVersion({
            id: `version-${version}`,
            version,
            manifest_hash: String(version).repeat(64),
            files:
              version === 1
                ? [
                    {
                      path: 'rules.md',
                      sha256: 'a'.repeat(64),
                      size_bytes: 42,
                      content_type: 'text/markdown',
                    },
                    {
                      path: 'old.md',
                      sha256: 'c'.repeat(64),
                      size_bytes: 8,
                      content_type: 'text/markdown',
                    },
                  ]
                : [
                    {
                      path: 'rules.md',
                      sha256: 'b'.repeat(64),
                      size_bytes: 48,
                      content_type: 'text/markdown',
                    },
                    {
                      path: 'new.md',
                      sha256: 'd'.repeat(64),
                      size_bytes: 12,
                      content_type: 'text/markdown',
                    },
                  ],
          }),
        })
    )
  })

  it('比较新增、移除和内容变化，技术详情默认折叠', async () => {
    const user = userEvent.setup()
    render(
      <TestQueryProvider>
        <ResourceVersionDiff
          workspaceId="ws1"
          resourceId="resource-1"
          currentVersion={1}
          versions={[
            makeSharedResourceVersion(),
            makeSharedResourceVersion({ id: 'version-2', version: 2 }),
          ]}
        />
      </TestQueryProvider>
    )
    expect(await screen.findByText('rules.md')).toBeInTheDocument()
    expect(screen.getByText('内容变化')).toBeInTheDocument()
    expect(screen.getByText('v2 新增')).toBeInTheDocument()
    expect(screen.getByText('v2 移除')).toBeInTheDocument()
    expect(
      screen.getByText('1'.repeat(64)).closest('details')
    ).not.toHaveAttribute('open')
    await user.click(screen.getByText('查看 v1 文件与高级详情'))
    expect(screen.getByText('1'.repeat(64)).closest('details')).toHaveAttribute(
      'open'
    )
  })
})
