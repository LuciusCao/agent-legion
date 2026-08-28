import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
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

function renderEditor(types: string[]) {
  const setDefinitionYaml = vi.fn()
  const node = {
    key: '_start',
    node_type: 'start',
    accepted_item_types: types,
  } as unknown as WorkflowNodeRecord
  render(
    <WorkflowNodeStartContractEditor
      node={node}
      definitionYaml={draftYaml}
      setDefinitionYaml={setDefinitionYaml}
    />
  )
  return setDefinitionYaml
}

describe('WorkflowNodeStartContractEditor', () => {
  it('renders user-facing labels and descriptions for every item type', () => {
    renderEditor(['material', 'ref'])

    expect(
      screen.getByText(/这个工作流接受哪些内容作为输入/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/决定「添加条目」对话框里提供哪些提交方式/)
    ).toBeInTheDocument()
    expect(screen.getByText(/需要管理员先配置外部服务连接/)).toBeInTheDocument()
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
})
