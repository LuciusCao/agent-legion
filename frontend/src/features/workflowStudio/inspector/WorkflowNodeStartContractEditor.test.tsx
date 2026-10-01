import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
import { MemoryRouter } from '../../../testing/TestMemoryRouter'
import { WorkflowNodeStartContractEditor } from './WorkflowNodeStartContractEditor'
import type { WorkflowNodeRecord } from '../../../types'

const draftYaml = [
  'key: demo',
  'nodes:',
  '  _start:',
  '    type: start',
  '    accepted_item_types: [material, ref]',
  '',
].join('\n')

function LocationProbe() {
  const { pathname, hash } = useLocation()
  return (
    <span data-testid="location-path">
      {pathname}
      {hash}
    </span>
  )
}

function renderEditor(types: string[], raw?: string) {
  const setDefinitionYaml = vi.fn()
  const node = {
    key: '_start',
    node_type: 'start',
    accepted_item_types: types,
  } as unknown as WorkflowNodeRecord
  render(
    <MemoryRouter>
      <WorkflowNodeStartContractEditor
        node={node}
        definitionYaml={
          raw ?? draftYaml.replace('[material, ref]', JSON.stringify(types))
        }
        setDefinitionYaml={setDefinitionYaml}
      />
      <LocationProbe />
    </MemoryRouter>
  )
  return setDefinitionYaml
}

describe('WorkflowNodeStartContractEditor', () => {
  it.each([
    'nodes: [',
    'nodes: []',
    'nodes: {_start: {type: start}}\nedges: [null]',
  ])('disables mutations on unsafe published fallback %s', (raw) => {
    const save = renderEditor(['material', 'text'], raw)
    const option = screen.getByRole('checkbox', { name: /直接输入需求/ })
    expect(option).toBeDisabled()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    fireEvent.click(option)
    expect(save).not.toHaveBeenCalled()
  })
  it('explicitly removes malformed text_input without rebuilding it from published fields', () => {
    const save = renderEditor(
      ['material', 'text'],
      draftYaml.replace('[material, ref]', '[ref, text]') +
        '    text_input: {template: 123}\n'
    )
    fireEvent.click(screen.getByRole('checkbox', { name: /直接输入需求/ }))
    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0][0]).not.toContain('text_input')
    expect(save.mock.calls[0][0]).toContain('- ref')
    expect(save.mock.calls[0][0]).not.toContain('- material')
  })
  it.each(['123', '[]', '[invalid]', '[material, 123]', '{bad: value}'])(
    'rejects malformed accepted_item_types %s before calling includes',
    (types) => {
      const save = renderEditor(
        ['material', 'text'],
        draftYaml.replace('[material, ref]', types)
      )
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(
        screen.getByRole('checkbox', { name: /直接输入需求/ })
      ).toBeDisabled()
      expect(save).not.toHaveBeenCalled()
    }
  )
  it('renders user-facing labels and descriptions for every item type', () => {
    renderEditor(['material', 'ref'])

    expect(
      screen.getByText(/这个工作流接受哪些内容作为输入/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/决定「添加条目」对话框里提供哪些提交方式/)
    ).toBeInTheDocument()
    expect(screen.getByText(/需要管理员先在/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /上传文件/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /外部平台内容/ })).toBeChecked()
    expect(
      screen.getByRole('checkbox', { name: /整个文件夹/ })
    ).not.toBeChecked()
    expect(screen.getByText('单个材料文件，浏览器直接上传')).toBeInTheDocument()
    expect(
      screen.getByText(/粘贴 ID 或链接引用外部平台内容/)
    ).toBeInTheDocument()
    expect(screen.getByText('保持目录结构，整体算一个条目')).toBeInTheDocument()
  })

  it('links the external-connection hint to the admin settings anchor', () => {
    renderEditor(['material', 'ref'])

    const link = screen.getByRole('link', { name: '全局设置 · 外部服务连接' })
    expect(link).toHaveAttribute('href', '/admin/settings#connections')

    fireEvent.click(link)
    expect(screen.getByTestId('location-path')).toHaveTextContent(
      '/admin/settings#connections'
    )
  })

  it('patches the draft YAML when an option is unchecked', () => {
    const setDefinitionYaml = renderEditor(['material', 'ref'])

    fireEvent.click(screen.getByRole('checkbox', { name: /外部平台内容/ }))

    expect(setDefinitionYaml).toHaveBeenCalledOnce()
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(nextYaml).toContain('accepted_item_types:')
    expect(nextYaml).toContain('- material')
    expect(nextYaml).not.toContain('- ref')
  })

  it('patches the draft YAML when an option is checked', () => {
    const setDefinitionYaml = renderEditor(['material', 'ref'])

    fireEvent.click(screen.getByRole('checkbox', { name: /整个文件夹/ }))

    expect(setDefinitionYaml).toHaveBeenCalledOnce()
    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(nextYaml).toContain('- bundle')
  })

  it('writes back in canonical material/ref/bundle order regardless of click order', () => {
    // 已选 ref+bundle，再勾 material：写回应是 material/ref/bundle，
    // 而不是把 material 追加到末尾。
    const setDefinitionYaml = renderEditor(['ref', 'bundle'])

    fireEvent.click(screen.getByRole('checkbox', { name: /上传文件/ }))

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    const materialAt = nextYaml.indexOf('- material')
    const refAt = nextYaml.indexOf('- ref')
    const bundleAt = nextYaml.indexOf('- bundle')
    expect(materialAt).toBeGreaterThanOrEqual(0)
    expect(materialAt).toBeLessThan(refAt)
    expect(refAt).toBeLessThan(bundleAt)
  })

  it('disables the only selected option to keep the contract non-empty', () => {
    const setDefinitionYaml = renderEditor(['ref'])

    expect(
      screen.getByRole('checkbox', { name: /外部平台内容/ })
    ).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /上传文件/ })).toBeEnabled()
    expect(screen.getByRole('checkbox', { name: /整个文件夹/ })).toBeEnabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /外部平台内容/ }))
    expect(setDefinitionYaml).not.toHaveBeenCalled()
  })

  it('clears the text_input block when 直接输入需求 is unticked', () => {
    const setDefinitionYaml = vi.fn()
    const node = {
      key: '_start',
      node_type: 'start',
      accepted_item_types: ['material', 'text'],
      text_input: { label: '创作需求', filename: '', template: '# 需求\n' },
    } as unknown as WorkflowNodeRecord
    const yamlWithBlock = [
      'key: demo',
      'nodes:',
      '  _start:',
      '    type: start',
      '    accepted_item_types: [material, text]',
      '    text_input:',
      '      label: 创作需求',
      '      template: "# 需求\\n"',
      '',
    ].join('\n')
    render(
      <MemoryRouter>
        <WorkflowNodeStartContractEditor
          node={node}
          definitionYaml={yamlWithBlock}
          setDefinitionYaml={setDefinitionYaml}
        />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole('checkbox', { name: /直接输入需求/ }))

    const nextYaml = setDefinitionYaml.mock.calls[0][0] as string
    expect(nextYaml).not.toContain('- text')
    expect(nextYaml).not.toContain('text_input')
    expect(nextYaml).toContain('- material')
  })
})
