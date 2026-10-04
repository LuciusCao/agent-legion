import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { components } from '../../../generated/api'
import { WorkflowNodeCodePreview } from './WorkflowNodeCodePreview'

type NodeCodeResponse = components['schemas']['WorkflowNodeCodeResponse']

const LONG_CODE = [
  '"""doc"""',
  'from workspace_libs.node_sdk import NodeContext, entrypoint',
  '',
  '',
  '@entrypoint',
  'def run(ctx: NodeContext) -> None:',
  '    ctx.checkpoint()',
  '    out = ctx.artifacts.write_json("intake_result.json", {"marker": "a-very-long-line-that-would-scroll-horizontally"})',
  '',
].join('\n')

function response(overrides: Partial<NodeCodeResponse> = {}) {
  return {
    origin: 'custom',
    code: LONG_CODE,
    version: 1,
    has_draft: false,
    draft_code: null,
    draft_version: null,
    ...overrides,
  } as NodeCodeResponse
}

describe('WorkflowNodeCodePreview（#770 窄栏只留摘要）', () => {
  it('窄栏不内嵌代码正文：只给行数 + 入口签名，不渲染 <pre>', () => {
    const { container } = render(
      <WorkflowNodeCodePreview nodeKey="intake" data={response()} />
    )
    const summary = screen.getByLabelText('节点代码摘要')
    expect(summary).toHaveTextContent('8 行')
    expect(summary).toHaveTextContent('入口')
    expect(
      within(summary).getByText('def run(ctx: NodeContext) -> None')
    ).toBeInTheDocument()
    // 函数体（长行）不出现在窄栏——base 上的滚动 <pre> 即红。
    expect(summary).not.toHaveTextContent('a-very-long-line')
    expect(container.querySelector('pre')).toBeNull()
  })

  it('「查看代码」一键进宽视图（全屏 dialog 带完整代码）', async () => {
    render(<WorkflowNodeCodePreview nodeKey="intake" data={response()} />)
    fireEvent.click(screen.getByRole('button', { name: '查看代码' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('节点代码 · intake')
    expect(dialog).toHaveTextContent('a-very-long-line')
  })

  it('无内置实现时摘要未发布草稿；无代码给占位且无宽视图入口', () => {
    const { rerender } = render(
      <WorkflowNodeCodePreview
        nodeKey="x"
        data={response({
          origin: 'none',
          code: '',
          draft_code: 'def run(ctx):\n    pass\n',
        })}
      />
    )
    expect(screen.getByText('def run(ctx)')).toBeInTheDocument()
    expect(screen.getByText('2 行')).toBeInTheDocument()

    rerender(
      <WorkflowNodeCodePreview
        nodeKey="x"
        data={response({ origin: 'none', code: '', draft_code: null })}
      />
    )
    expect(screen.getByText('暂无代码')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看代码' })).toBeNull()
  })
})
