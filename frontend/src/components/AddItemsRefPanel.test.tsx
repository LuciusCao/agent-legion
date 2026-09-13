import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, act } from '@testing-library/react'
import { useLocation } from 'react-router-dom'

import { AddItemsRefPanel } from './AddItemsRefPanel'
import { getConnectionKeys } from '../api/connections'
import { MemoryRouter } from '../testing/TestMemoryRouter'

vi.mock('../api/connections', () => ({
  getConnectionKeys: vi.fn(),
}))

const mockGetConnectionKeys = vi.mocked(getConnectionKeys)

function LocationProbe() {
  const { pathname, hash } = useLocation()
  return (
    <span data-testid="location-path">
      {pathname}
      {hash}
    </span>
  )
}

function renderPanel(onConnectionKeyChange = vi.fn()) {
  return {
    onConnectionKeyChange,
    ...render(
      <MemoryRouter>
        <AddItemsRefPanel
          connectionKey=""
          refText=""
          onConnectionKeyChange={onConnectionKeyChange}
          onRefTextChange={vi.fn()}
        />
        <LocationProbe />
      </MemoryRouter>
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

  it('renders an admin-settings link when the instance has zero keys', async () => {
    // #593：空态不留死胡同——下拉占位之外，字段下方出现指向
    // /admin/settings#connections 的跳转链接，点击可导航。
    // 注意 loading 态的 datalist 文本框隐式 role 也是 combobox，等待链接
    // 出现才是「空列表已就绪」的信号。
    mockGetConnectionKeys.mockResolvedValue({ keys: [] } as never)
    renderPanel()

    const link = await screen.findByRole('link', {
      name: '全局设置 · 外部服务连接',
    })
    expect(link).toHaveAttribute('href', '/admin/settings#connections')
    expect(screen.getByText(/需要管理员先到/)).toBeInTheDocument()
    expect(screen.getByText(/配置。/)).toBeInTheDocument()

    fireEvent.click(link)
    expect(screen.getByTestId('location-path')).toHaveTextContent(
      '/admin/settings#connections'
    )
  })

  it('does not render the empty-state link when keys exist', async () => {
    mockGetConnectionKeys.mockResolvedValue({ keys: ['cms-a'] } as never)
    renderPanel()
    // MUI select 的 combobox 带 aria-haspopup，与 loading 态的 datalist
    // 文本框区分开；等到 select 形态再断言链接不存在。
    await waitFor(() =>
      expect(screen.getByRole('combobox')).toHaveAttribute(
        'aria-haspopup',
        'listbox'
      )
    )

    expect(
      screen.queryByRole('link', { name: '全局设置 · 外部服务连接' })
    ).not.toBeInTheDocument()
  })
})
