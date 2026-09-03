import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react'

import { AddItemsRefPanel } from './AddItemsRefPanel'
import { getConnectionKeys } from '../api/connections'
import { TestQueryProvider } from '../testing/testQueryClient'

vi.mock('../api/connections', () => ({
  getConnectionKeys: vi.fn(),
}))

const mockGetConnectionKeys = vi.mocked(getConnectionKeys)

function renderPanel(onConnectionKeyChange = vi.fn()) {
  return {
    onConnectionKeyChange,
    ...render(
      <TestQueryProvider>
        <AddItemsRefPanel
          connectionKey=""
          refText=""
          onConnectionKeyChange={onConnectionKeyChange}
          onRefTextChange={vi.fn()}
        />
      </TestQueryProvider>
    ),
  }
}

/**
 * 打开 MUI Select 菜单。jsdom 里 focus 时序与浏览器不同，菜单 portal 的
 * 渲染可能落后一次 mouseDown（组件因 query 数据到达重渲），失败时重发
 * mouseDown 直到 listbox 出现（上限 10 次）。
 */
async function openMenu(): Promise<void> {
  for (let attempt = 0; attempt < 10; attempt++) {
    await act(async () => {
      fireEvent.mouseDown(screen.getByRole('combobox'))
    })
    if (document.querySelector('[role="listbox"]')) return
  }
  throw new Error('select menu did not open')
}

/** 当前菜单里的选项文本（menu portal 在 body 下，不在 container 内）。 */
function menuOptions(): string[] {
  return [...document.querySelectorAll('[role="option"]')].map(
    (option) => option.textContent ?? ''
  )
}

describe('AddItemsRefPanel', () => {
  beforeEach(() => {
    mockGetConnectionKeys.mockReset()
  })

  it('renders a text field while the key list is loading', () => {
    // 接口未返回前先渲染手写文本框，不阻塞输入。
    mockGetConnectionKeys.mockReturnValue(new Promise(() => {}) as never)
    renderPanel()
    const input = screen.getByLabelText('连接 Key')
    expect(input.tagName).toBe('INPUT')
    expect(input).toHaveAttribute(
      'placeholder',
      'workflow 绑定的外部服务连接 key'
    )
  })

  it('degrades to a writable text input when the endpoint fails', async () => {
    mockGetConnectionKeys.mockRejectedValue(new Error('401') as never)
    const { onConnectionKeyChange } = renderPanel()
    await waitFor(() => expect(mockGetConnectionKeys).toHaveBeenCalledTimes(1))
    // 失败后仍是文本框（可手写），不出现下拉。
    const input = screen.getByLabelText('连接 Key')
    expect(input.tagName).toBe('INPUT')
    fireEvent.change(input, { target: { value: 'cms-manual' } })
    expect(onConnectionKeyChange).toHaveBeenCalledWith('cms-manual')
  })

  it('selects a key from the dropdown with multiple candidates', async () => {
    mockGetConnectionKeys.mockResolvedValue({
      keys: ['cms-a', 'cms-b'],
    } as never)
    const { onConnectionKeyChange } = renderPanel()
    // 请求成功后渲染为 MUI select（combobox role）。
    await screen.findByRole('combobox')
    await openMenu()
    expect(menuOptions()).toEqual(['cms-a', 'cms-b'])
    const option = document.querySelectorAll('[role="option"]')[1]
    fireEvent.click(option)
    expect(onConnectionKeyChange).toHaveBeenCalledWith('cms-b')
    expect(mockGetConnectionKeys).toHaveBeenCalledTimes(1)
  })

  it('auto-selects the single candidate', async () => {
    mockGetConnectionKeys.mockResolvedValue({ keys: ['cms-only'] } as never)
    const { onConnectionKeyChange } = renderPanel()
    await screen.findByRole('combobox')
    await waitFor(() =>
      expect(onConnectionKeyChange).toHaveBeenCalledWith('cms-only')
    )
  })

  it('shows an empty-state option when the instance has zero keys', async () => {
    mockGetConnectionKeys.mockResolvedValue({ keys: [] } as never)
    renderPanel()
    await screen.findByRole('combobox')
    await openMenu()
    expect(menuOptions()).toEqual(['（实例还没有外部服务连接）'])
  })
})
