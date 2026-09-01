/**
 * QuestionContentPanel（#328 改造后）是官方内置 bundle 的宿主包装器：
 * 渲染 PreviewPanelHost 并把 QUESTION_PANEL_BUNDLE 作为 srcDoc 传入。
 * bundle 内部行为（渲染/交互/gating）由
 * features/previewPanel/builtin/questionPanel.test.ts 覆盖；原 React 实现的
 * 深入用例（regression/review）随之退役。
 */
import { describe, it, expect } from 'vitest'
import { act, render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { QuestionContentPanel } from './QuestionContentPanel'
import { QUESTION_PANEL_BUNDLE } from '../../features/previewPanel/builtin/questionPanelBundle'
import { TestQueryProvider } from '../../testing/testQueryClient'

function renderPanel(ui: ReactElement) {
  return render(ui, { wrapper: TestQueryProvider })
}

describe('QuestionContentPanel（内置 bundle 宿主）', () => {
  it('renders the builtin bundle in the sandboxed preview host', async () => {
    const { container } = renderPanel(<QuestionContentPanel jobId="job-1" />)

    const host = container.querySelector('[data-testid="preview-panel-host"]')
    expect(host).not.toBeNull()
    const iframe = container.querySelector('iframe')
    expect(iframe).not.toBeNull()
    // srcdoc = bundle 原文 + 宿主注入的 CSP meta（出站网络红线），断言包含。
    const srcdoc = iframe!.getAttribute('srcdoc') ?? ''
    expect(srcdoc).toContain('Content-Security-Policy')
    expect(srcdoc).toContain(QUESTION_PANEL_BUNDLE.slice(0, 200))
    expect(iframe!.getAttribute('title')).toBe('题目内容')
    // 冲刷宿主内部的异步更新（detail 查询、iframe load）进 act。
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
  })
})
