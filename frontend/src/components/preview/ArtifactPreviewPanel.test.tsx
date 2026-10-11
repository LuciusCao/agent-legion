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

describe('ArtifactPreviewPanel', () => {
  beforeEach(() => {
    mockPreviewHidden.value = []
    mockToggleArtifact.mockClear()
  })

  it('渲染每个 artifact 一张卡片，含类型徽标', async () => {
    mockFetchJobArtifactText.mockResolvedValue(
      textOf(JSON.stringify({ ok: true }))
    )
    renderPanel(
      <ArtifactPreviewPanel
        jobId="j1"
        detail={makeDetail(['questions.json', 'frame.png'])}
      />
    )

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

  it('无产物时渲染空态而不是空白', () => {
    renderPanel(<ArtifactPreviewPanel jobId="j1" detail={makeDetail([])} />)

    expect(screen.getByText('暂无产物文件')).toBeInTheDocument()
  })

  it('workspace 预览配置隐藏对应卡片', () => {
    mockPreviewHidden.value = ['questions.json']
    renderPanel(
      <ArtifactPreviewPanel
        jobId="j1"
        detail={makeDetail(['questions.json', 'frame.png'])}
        workspaceId="ws1"
      />
    )

    expect(screen.queryByText('questions.json')).not.toBeInTheDocument()
    expect(screen.getByText('frame.png')).toBeInTheDocument()
    expect(screen.getByText('1 个文件')).toBeInTheDocument()
  })

  it('勾选菜单切换产物可见性（写 workspace 配置）', async () => {
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

    expect(screen.getByText('暂无产物文件')).toBeInTheDocument()
    expect(
      screen.queryByTestId('artifact-preview-card')
    ).not.toBeInTheDocument()
  })

  it('json 解析失败时按原文展示', async () => {
    mockFetchJobArtifactText.mockResolvedValue(textOf('not-json{{'))
    renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['broken.json'])} />
    )

    await waitFor(() => {
      expect(screen.getByText('not-json{{')).toBeInTheDocument()
    })
  })

  it('合法 json 可切到源码态查看原文（#777）', async () => {
    const raw = '{\n  "question_id": "q1",\n  "grade": 5\n}'
    mockFetchJobArtifactText.mockResolvedValue(textOf(raw))
    renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['review.json'])} />
    )

    // 默认树视图：键名不带引号渲染，无原文 <pre>。
    expect(await screen.findByText('question_id')).toBeInTheDocument()
    expect(document.querySelector('pre')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '源码' }))
    const pre = document.querySelector('pre')
    expect(pre?.textContent).toBe(raw)
    expect(screen.queryByText('全部展开')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(document.querySelector('pre')).not.toBeInTheDocument()
    expect(screen.getByText('question_id')).toBeInTheDocument()
  })

  // 守卫：卡片按 name 复用，但换 job 后文本查询进入加载态会卸载正文，
  // 源码态不应跨 job 残留。
  it('同名 json 换 job 后源码态重置回树视图', async () => {
    mockFetchJobArtifactText.mockResolvedValue(textOf('{"question_id":"q1"}'))
    const { rerender } = renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['review.json'])} />
    )
    expect(await screen.findByText('question_id')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '源码' }))
    expect(document.querySelector('pre')).toBeInTheDocument()

    rerender(
      <ArtifactPreviewPanel jobId="j2" detail={makeDetail(['review.json'])} />
    )
    expect(await screen.findByText('question_id')).toBeInTheDocument()
    expect(document.querySelector('pre')).not.toBeInTheDocument()
  })

  it('图片加载失败展示错误占位并可重试', async () => {
    renderPanel(
      <ArtifactPreviewPanel jobId="j1" detail={makeDetail(['frame.png'])} />
    )

    const img = await screen.findByRole('img', { name: 'frame.png' })
    fireEvent.error(img)
    expect(screen.getByText('媒体加载失败')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '新窗口打开' })).toHaveAttribute(
      'href',
      '/api/jobs/j1/raw-artifacts/frame.png'
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

    const link = screen.getByRole('link', { name: '下载' })
    expect(link).toHaveAttribute('href', '/api/jobs/j1/raw-artifacts/frame.png')
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

    await waitFor(() => {
      expect(screen.getByText(/已截断/)).toBeInTheDocument()
    })
    const pre = document.querySelector('pre')
    expect(pre?.textContent?.length).toBe(512 * 1024)
  })
})
