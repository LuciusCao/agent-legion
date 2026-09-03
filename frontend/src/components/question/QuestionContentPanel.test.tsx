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
import { TestQueryProvider } from '../../testing/testQueryClient'
import { expectConsoleError, expectConsoleWarning } from '../../test-setup'

function renderPanel(ui: ReactElement) {
  return render(ui, { wrapper: TestQueryProvider })
}

describe('QuestionContentPanel（内置 bundle 宿主）', () => {
  it('renders the builtin bundle in the sandboxed preview host', async () => {
    // 宿主内部 detail 查询解析可能脱离 act 包裹（known noise），声明预期。
    expectConsoleWarning(/not wrapped in act/)
    expectConsoleError(/not wrapped in act/)
    const { container } = renderPanel(<QuestionContentPanel jobId="job-1" />)

    const host = container.querySelector('[data-testid="preview-panel-host"]')
    expect(host).not.toBeNull()
    const iframe = container.querySelector('iframe')
    expect(iframe).not.toBeNull()
    // srcdoc = bundle 经 DOMParser 定位 head + 注入 CSP meta 后的序列化，
    // 断言语义片段而非整串相等：序列化会丢弃 doctype 后的顶层注释等细节。
    const srcdoc = iframe!.getAttribute('srcdoc') ?? ''
    expect(srcdoc).toContain('Content-Security-Policy')
    expect(srcdoc).toContain('<body>')
    expect(srcdoc).toContain('id="app"')
    expect(iframe!.getAttribute('title')).toBe('题目内容')
    // 冲刷宿主内部的异步更新（detail 查询、iframe load）进 act。
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
  })
})
