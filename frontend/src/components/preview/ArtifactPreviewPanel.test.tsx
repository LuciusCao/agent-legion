import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { ReactElement } from 'react'
import { ArtifactPreviewPanel } from './ArtifactPreviewPanel'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { makeJob } from '../../testing/fixtures'
import type { JobDetail } from '../../types/jobTypes'

function renderPanel(ui: ReactElement) {
  return render(ui, { wrapper: TestQueryProvider })
}

const mockFetchJobArtifactText = vi.fn()

const mockPreviewHidden = vi.hoisted(() => ({ value: [] as string[] }))
const mockToggleArtifact = vi.fn()

vi.mock('../../hooks/useWorkspacePreviewConfig', () => ({
  useWorkspacePreviewConfig: () => ({
    previewHidden: mockPreviewHidden.value,
    toggleArtifact: mockToggleArtifact,
    visibleArtifacts: (artifacts: string[]) =>
      artifacts.filter((name) => !mockPreviewHidden.value.includes(name)),
  }),
}))

// 文本预览走 ../../api/jobArtifactText 的有界 Range 读取（不经 barrel），
// mock 必须打在同一模块上。
vi.mock('../../api/jobArtifactText', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../api/jobArtifactText')>()
  return {
    ...mod,
    fetchJobArtifactText: (...args: unknown[]) =>
      mockFetchJobArtifactText(...args),
  }
})

/** 文本预览 mock 的统一返回形（未截断全文）。 */
function textOf(content: string) {
  return { content, truncated: false, total: content.length }
}

function makeDetail(artifacts: string[]): JobDetail {
  return {
    job: makeJob({ status: 'completed' }),
    nodes: [],
    runs: [],
    artifacts,
  }
}

/** 展开折叠面板（#255 起默认收起为一行摘要）。 */
function expandPanel() {
  fireEvent.click(screen.getByRole('button', { name: /产物预览/ }))
}

describe('ArtifactPreviewPanel', () => {
  beforeEach(() => {
    mockPreviewHidden.value = []
    mockToggleArtifact.mockClear()
  })

  it('默认折叠为一行摘要（含文件数），展开后渲染每个 artifact 一张卡片', async () => {
    mockFetchJobArtifactText.mockResolvedValue(
      textOf(JSON.stringify({ ok: true }))
    )
    renderPanel(
      <ArtifactPreviewPanel
        jobId="j1"
        detail={makeDetail(['questions.json', 'frame.png'])}
      />
    )

    // 默认收起：摘要行可见、卡片不挂载（不发起产物读取）。
    expect(screen.getByText('2 个文件')).toBeInTheDocument()
    expect(
      screen.queryByTestId('artifact-preview-card')
    ).not.toBeInTheDocument()
    expect(mockFetchJobArtifactText).not.toHaveBeenCalled()

    expandPanel()
    expect(await screen.findByText('questions.json')).toBeInTheDocument()
    expect(screen.getByText('frame.png')).toBeInTheDocument()
    expect(screen.getByText('JSON')).toBeInTheDocument()
    expect(screen.getByText('图片')).toBeInTheDocument()
    // JSON 卡片挂载 JsonTree（解析后的树渲染键名）。
    await waitFor(() => {
      expect(mockFetchJobArtifactText).toHaveBeenCalledWith(
        'j1',
        'questions.json',
        expect.any(Number)
      )
    })
  })

  it('无产物时折叠态不渲染空态正文，展开后渲染空态', () => {
    renderPanel(<ArtifactPreviewPanel jobId="j1" detail={makeDetail([])} />)

    expect(screen.getByText('0 个文件')).toBeInTheDocument()
    expect(screen.queryByText('暂无产物文件')).not.toBeInTheDocument()
    expandPanel()
    expect(screen.getByText('暂无产物文件')).toBeInTheDocument()
  })

  it('workspace 预览配置隐藏对应卡片（计数只含可见文件）', () => {
    mockPreviewHidden.value = ['questions.json']
    renderPanel(
      <ArtifactPreviewPanel
        jobId="j1"
        detail={makeDetail(['questions.json', 'frame.png'])}
        workspaceId="ws1"
      />
    )

    expandPanel()
    expect(screen.queryByText('questions.json')).not.toBeInTheDocument()
    expect(screen.getByText('frame.png')).toBeInTheDocument()
    expect(screen.getByText('1 个文件')).toBeInTheDocument()
  })

  it('结构化面板消费的产物默认去重（#255 场景 3）：原始卡片不展示、可勾选恢复', async () => {
    renderPanel(
      <ArtifactPreviewPanel
        jobId="j1"
        detail={makeDetail([
          'questions.json',
          'comprehension_info.json',
          'key_info_review_report.json',
          'frame.png',
        ])}
        structuredHidden={[
          'questions.json',
          'comprehension_info.json',
          'key_info_review_report.json',
        ]}
      />
    )

    // 摘要：1 个可见（frame.png）+ 3 个已在上方展示。
    expect(screen.getByText('1 个文件')).toBeInTheDocument()
    expect(screen.getByText('另 3 个已在上方展示')).toBeInTheDocument()
    expandPanel()
    expect(screen.queryByText('questions.json')).not.toBeInTheDocument()
    expect(
      screen.queryByText('comprehension_info.json')
    ).not.toBeInTheDocument()
    expect(
      screen.queryByText('key_info_review_report.json')
    ).not.toBeInTheDocument()
    expect(screen.getByText('frame.png')).toBeInTheDocument()

    // 勾选菜单恢复：会话态（不写 workspace 配置）。MUI Menu 常驻 DOM，
    // 卡片标题与菜单项同名，断言用 allBy；两轮勾选间用 Esc 关菜单。
    fireEvent.click(screen.getByRole('button', { name: '配置预览产物' }))
    fireEvent.click(
      await screen.findByRole('menuitem', { name: /questions\.json/ })
    )
    fireEvent.keyDown(document.activeElement ?? document.body, {
      key: 'Escape',
    })
    expect(mockToggleArtifact).not.toHaveBeenCalled()
    expect(await screen.findAllByText('questions.json')).not.toHaveLength(0)
    expect(screen.getByText('2 个文件')).toBeInTheDocument()

    // 普通产物的勾选仍走 workspace 配置。
    fireEvent.click(screen.getByRole('button', { name: '配置预览产物' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /frame\.png/ }))
    expect(mockToggleArtifact).toHaveBeenCalledWith('frame.png', false)
  })

  it('无结构化名单时（非 question 实体，#255 场景 1）：全部产物默认可见、默认折叠', async () => {
    mockFetchJobArtifactText.mockResolvedValue(
      textOf(JSON.stringify({ ok: true }))
    )
    renderPanel(
      <ArtifactPreviewPanel
        jobId="j1"
        detail={makeDetail(['notes.md', 'frame.png'])}
      />
    )

    // 隐藏名单为空：全部计入可见数，摘要行不出现去重提示。
    expect(screen.getByText('2 个文件')).toBeInTheDocument()
    expect(screen.queryByText(/已在上方展示/)).not.toBeInTheDocument()
    expandPanel()
    expect(await screen.findByText('notes.md')).toBeInTheDocument()
    expect(screen.getByText('frame.png')).toBeInTheDocument()
  })

  it('勾选菜单切换普通产物可见性（写 workspace 配置）', async () => {
    renderPanel(
      <ArtifactPreviewPanel
        jobId="j1"
        detail={makeDetail(['questions.json', 'frame.png'])}
        workspaceId="ws1"
      />
    )

    fireEvent.click(screen.getByRole('button', { name: '配置预览产物' }))
    const item = await screen.findByRole('menuitem', {
      name: /questions\.json/,
    })
    fireEvent.click(item)
    expect(mockToggleArtifact).toHaveBeenCalledWith('questions.json', false)
  })

  it('detail 为 null 时不渲染卡片列表（等待 detail）', () => {
    renderPanel(<ArtifactPreviewPanel jobId="j1" detail={null} />)

    expandPanel()
    expect(screen.getByText('暂无产物文件')).toBeInTheDocument()
    expect(
      screen.queryByTestId('artifact-preview-card')
    ).not.toBeInTheDocument()
  })

  it('json 解析失败时按格式化原文展示（着色不吞内容）', async () => {
    mockFetchJobArtifactText.mockResolvedValue(textOf('not-json{{'))
    renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['broken.json'])} />
    )

    expandPanel()
    const pre = await waitFor(() => {
      const node = document.querySelector('pre')
      expect(node?.textContent).toBe('not-json{{')
      return node as HTMLElement
    })
    expect(pre.querySelectorAll('span').length).toBe(0)
  })

  it('图片加载失败展示错误占位并可重试', async () => {
    renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['frame.png'])} />
    )

    expandPanel()
    const img = await screen.findByRole('img', { name: 'frame.png' })
    fireEvent.error(img)
    expect(screen.getByText('媒体加载失败')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '新窗口打开' })).toHaveAttribute(
      'href',
      '/api/jobs/j1/artifacts/frame.png/raw'
    )

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    // 重试后重新挂载 <img>（失败占位消失）。
    await waitFor(() => {
      expect(screen.queryByText('媒体加载失败')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('img', { name: 'frame.png' })).toBeInTheDocument()
  })

  it('卡片头部提供原始字节下载链接', () => {
    renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['frame.png'])} />
    )

    expandPanel()
    const link = screen.getByRole('link', { name: '下载' })
    expect(link).toHaveAttribute('href', '/api/jobs/j1/artifacts/frame.png/raw')
  })

  it('文本超长时截断并显示提示', async () => {
    const long = 'x'.repeat(512 * 1024 + 100)
    // 有界读取由 api 层截断：组件拿到的是已截断文本 + 服务端总数。
    mockFetchJobArtifactText.mockResolvedValue({
      content: long.slice(0, 512 * 1024),
      truncated: true,
      total: long.length,
    })
    renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['big.log'])} />
    )

    expandPanel()
    await waitFor(() => {
      expect(screen.getByText(/已截断/)).toBeInTheDocument()
    })
    const pre = document.querySelector('pre')
    expect(pre?.textContent?.length).toBe(512 * 1024)
  })
})
