import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { WorkflowStudioSharedMaterialsButton } from './WorkflowStudioSharedMaterialsDrawer'
import {
  getWorkspaceSharedMaterialFile,
  getWorkspaceSharedMaterials,
  propagateWorkspaceSharedMaterials,
} from '../../../api'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'

vi.mock('../../../api', () => ({
  getWorkspaceSharedMaterials: vi.fn(),
  getWorkspaceSharedMaterialFile: vi.fn(),
  propagateWorkspaceSharedMaterials: vi.fn(),
}))

const mockGetShared = vi.mocked(getWorkspaceSharedMaterials)
const mockGetFile = vi.mocked(getWorkspaceSharedMaterialFile)
const mockPropagate = vi.mocked(propagateWorkspaceSharedMaterials)

const WORKSPACE_ID = 'demo_video_workflow'

const populated = {
  workspace_id: WORKSPACE_ID,
  map: {
    version: 1,
    materials: [
      {
        source: 'references/style.md',
        skills: [
          { skill: 'write-script', status: 'synced' as const },
          { skill: 'review-script', status: 'pending_sync' as const },
          { skill: 'old-skill', status: 'missing_in_skill' as const },
          { skill: 'ghost-skill', status: 'skill_not_found' as const },
        ],
      },
      {
        source: 'references/synced.md',
        skills: [{ skill: 'write-script', status: 'synced' as const }],
      },
      {
        // map 引用但 _shared 里不存在的 source。
        source: 'references/gone.md',
        skills: [{ skill: 'lost-skill', status: 'pending_sync' as const }],
      },
    ],
  },
  files: [
    {
      path: 'references/style.md',
      size: 17,
      modified_at: '2026-09-01T08:00:00+00:00',
    },
    {
      path: 'references/synced.md',
      size: 5,
      modified_at: '2026-09-01T08:00:00+00:00',
    },
    {
      // scripts/ 组：未被任何 map 条目引用的文件。
      path: 'scripts/validate.sh',
      size: 3,
      modified_at: '2026-09-01T08:00:00+00:00',
    },
    {
      // 「其他」组：_shared 根文件与其他子目录文件（map 管不到它们）。
      path: 'notes.md',
      size: 2,
      modified_at: '2026-09-01T08:00:00+00:00',
    },
    {
      path: 'docs/guide.md',
      size: 4,
      modified_at: '2026-09-01T08:00:00+00:00',
    },
  ],
}

function renderEntry() {
  return render(
    <MemoryRouter
      initialEntries={[`/workspaces/${WORKSPACE_ID}/workflow-studio`]}
    >
      <Routes>
        <Route
          path="/workspaces/:workspaceId/workflow-studio"
          element={<WorkflowStudioSharedMaterialsButton />}
        />
      </Routes>
    </MemoryRouter>
  )
}

async function openDrawer() {
  renderEntry()
  fireEvent.click(screen.getByRole('button', { name: 'Skill 共享材料' }))
  // 抽屉打开后才发起查询。
  await waitFor(() => expect(mockGetShared).toHaveBeenCalledWith(WORKSPACE_ID))
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetShared.mockResolvedValue(populated)
  mockGetFile.mockImplementation((_ws, path) =>
    Promise.resolve({
      path,
      size: 17,
      content:
        path === 'map.json'
          ? '{"version": 1, "materials": []}\n'
          : '# house style v2\n',
      truncated: false,
    })
  )
  mockPropagate.mockResolvedValue({
    workspace_id: WORKSPACE_ID,
    results: [
      {
        skill: 'review-script',
        status: 'synced',
        tag: 'v1.0.1',
        detail: null,
        synced_files: ['references/style.md'],
      },
      {
        skill: 'old-skill',
        status: 'failed',
        tag: null,
        detail: 'repo has uncommitted changes',
        synced_files: [],
      },
    ],
  })
})

describe('WorkflowStudioSharedMaterialsDrawer', () => {
  it('renders only the entry button until clicked', () => {
    renderEntry()
    expect(
      screen.getByRole('button', { name: 'Skill 共享材料' })
    ).toBeInTheDocument()
    expect(screen.queryByText('参考材料')).not.toBeInTheDocument()
    expect(mockGetShared).not.toHaveBeenCalled()
  })

  it('shows the empty state when the workspace never opted into _shared', async () => {
    mockGetShared.mockResolvedValue({
      workspace_id: WORKSPACE_ID,
      map: null,
      files: [],
    })
    await openDrawer()
    expect(await screen.findByText(/尚未启用共享材料/)).toBeInTheDocument()
  })

  it('merges mapping into the file list with inline drift badges', async () => {
    await openDrawer()
    // 材料组行内只显文件名（组标题承载目录语义），同一文件只出现一次。
    const item = (await screen.findByText('style.md')).closest('li')
    expect(item).not.toBeNull()
    const row = within(item as HTMLElement)
    expect(row.queryByText('references/style.md')).not.toBeInTheDocument()
    expect(row.getByText('write-script · 一致')).toBeInTheDocument()
    expect(row.getByText('review-script · 待同步')).toBeInTheDocument()
    expect(row.getByText('old-skill · 仓库缺失')).toBeInTheDocument()
    expect(row.getByText('ghost-skill · Skill 缺失')).toBeInTheDocument()
    // 未映射文件与缺失源行都有交代（缺失源行保留完整 source 路径）。
    expect(screen.getAllByText('未映射').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('缺失源')).toBeInTheDocument()
    expect(screen.getByText('references/gone.md')).toBeInTheDocument()
    // 大小 chip 仍在行内。
    expect(item?.textContent).toContain('17 B')
  })

  it('groups rows under titled headers with descriptions, hiding empty groups', async () => {
    await openDrawer()
    await screen.findByText('style.md')
    // 大中文组标题 + 目录描述小字。
    expect(
      screen.getByRole('heading', { name: '参考材料' })
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '脚本' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '其他' })).toBeInTheDocument()
    expect(
      screen.getByText(/_shared\/references\/ 目录下的共享内容/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/_shared\/scripts\/ 目录下的共享内容/)
    ).toBeInTheDocument()
    expect(screen.getByText(/_shared\/ 根目录下的其他文件/)).toBeInTheDocument()
    // 组归属：脚本组行内只显文件名，其他组保留相对路径。
    const scriptsGroup = screen
      .getByRole('heading', { name: '脚本' })
      .closest('section')
    expect(scriptsGroup?.textContent).toContain('validate.sh')
    expect(scriptsGroup?.textContent).not.toContain('scripts/validate.sh')
    const otherGroup = screen
      .getByRole('heading', { name: '其他' })
      .closest('section')
    expect(otherGroup?.textContent).toContain('notes.md')
    expect(otherGroup?.textContent).toContain('docs/guide.md')
    // 其他组文件不参与映射：正常显示「未映射」。
    expect(
      within(otherGroup as HTMLElement).getAllByText('未映射').length
    ).toBe(2)
  })

  it('pins a map.json row on top with its purpose note, opening the raw file', async () => {
    await openDrawer()
    await screen.findByText('style.md')
    const mapRow = screen.getByTestId('shared-material-map.json')
    // 用途说明在行下；无 drift 徽标、无传播动作。
    expect(mapRow.textContent).toContain('声明材料 → skills 的映射关系')
    expect(mapRow.textContent).not.toContain('一致')
    expect(
      within(mapRow).queryByRole('button', { name: /同步/ })
    ).not.toBeInTheDocument()
    // 置顶：在分组标题之前。
    expect(
      mapRow.compareDocumentPosition(
        screen.getByRole('heading', { name: '参考材料' })
      ) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    // 点击查看原文：同一个内容 Dialog，顶部完整路径 map.json。
    fireEvent.click(within(mapRow).getByRole('button'))
    expect(mockGetFile).toHaveBeenCalledWith(WORKSPACE_ID, 'map.json')
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('map.json')).toBeInTheDocument()
    expect(await within(dialog).findByText(/"version": 1/)).toBeInTheDocument()
  })

  it('omits group headers for groups without rows', async () => {
    mockGetShared.mockResolvedValue({
      workspace_id: WORKSPACE_ID,
      map: { version: 1, materials: [] },
      files: [
        {
          path: 'references/only.md',
          size: 1,
          modified_at: '2026-09-01T08:00:00+00:00',
        },
      ],
    })
    await openDrawer()
    await screen.findByText('only.md')
    expect(
      screen.getByRole('heading', { name: '参考材料' })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: '脚本' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: '其他' })
    ).not.toBeInTheDocument()
  })

  it('opens the read-only file viewer with the full path and closes it', async () => {
    await openDrawer()
    fireEvent.click(await screen.findByText('style.md'))
    // Dialog 顶部显示完整路径，再往下是文本内容。
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('references/style.md')).toBeInTheDocument()
    expect(
      await within(dialog).findByText('# house style v2')
    ).toBeInTheDocument()
    expect(mockGetFile).toHaveBeenCalledWith(
      WORKSPACE_ID,
      'references/style.md'
    )
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    await waitFor(() => {
      expect(screen.queryByText('# house style v2')).not.toBeInTheDocument()
    })
  })

  it('offers propagate only on rows with behind/missing skills', async () => {
    await openDrawer()
    await screen.findByText('style.md')
    // 有待同步/仓库缺失徽标的行有动作。
    expect(
      screen.getByRole('button', { name: '同步 references/style.md' })
    ).toBeInTheDocument()
    // 全部一致的行、未映射的行、缺失源行都没有动作。
    expect(
      screen.queryByRole('button', { name: '同步 references/synced.md' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '同步 scripts/validate.sh' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '同步 notes.md' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '同步 references/gone.md' })
    ).not.toBeInTheDocument()
  })

  it('propagates after confirmation and shows per-skill results', async () => {
    await openDrawer()
    fireEvent.click(
      await screen.findByRole('button', { name: '同步 references/style.md' })
    )
    // 轻确认：说明会 commit + 打新 tag（两种起始情形都准确的表述）。
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('commit')
    expect(dialog.textContent).toContain('references/style.md')
    expect(dialog.textContent).toContain(
      '最高版本 +0.0.1；无版本 tag 的仓库从 v0.1.0 起'
    )
    fireEvent.click(screen.getByRole('button', { name: '同步并打 tag' }))

    await waitFor(() =>
      expect(mockPropagate).toHaveBeenCalledWith(WORKSPACE_ID, [
        'references/style.md',
      ])
    )
    // 逐 skill 结果：成功带新 tag，失败带原因。
    const status = await screen.findByRole('status')
    expect(status.textContent).toContain('review-script：已同步 → v1.0.1')
    expect(status.textContent).toContain(
      'old-skill：失败（repo has uncommitted changes）'
    )
    // 共享材料查询被失效重取（初始 1 次 + invalidate 后 refetch）。
    await waitFor(() =>
      expect(mockGetShared.mock.calls.length).toBeGreaterThanOrEqual(2)
    )
    // 清除结果。
    fireEvent.click(screen.getByRole('button', { name: '清除同步结果' }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('closes the drawer from the header button', async () => {
    await openDrawer()
    await screen.findByText('style.md')
    fireEvent.click(
      screen.getByRole('button', { name: 'close shared materials panel' })
    )
    await waitFor(() => {
      expect(screen.queryByText('style.md')).not.toBeInTheDocument()
    })
  })

  it('surfaces query errors', async () => {
    mockGetShared.mockRejectedValue(new Error('boom'))
    await openDrawer()
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})
