import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { triggerDownload } from './download'

describe('triggerDownload', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    global.URL.createObjectURL = vi.fn(() => 'blob:mock')
    global.URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('downloads file via anchor click when fetch succeeds', async () => {
    const blob = new Blob(['content'])
    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(blob),
      headers: new Headers({
        'content-disposition': 'attachment; filename="test.txt"',
      }),
    } as Response)

    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})
    const appendChildSpy = vi
      .spyOn(document.body, 'appendChild')
      .mockImplementation(() => document.createElement('a'))
    const removeChildSpy = vi
      .spyOn(document.body, 'removeChild')
      .mockImplementation(() => document.createElement('a'))

    await triggerDownload('https://example.com/file.txt')

    expect(global.fetch).toHaveBeenCalledWith('https://example.com/file.txt', {
      cache: 'no-store',
    })
    expect(clickSpy).toHaveBeenCalled()
    expect(appendChildSpy).toHaveBeenCalled()
    expect(removeChildSpy).toHaveBeenCalled()
  })

  it('falls back to window.open when fetch fails', async () => {
    vi.mocked(global.fetch).mockRejectedValue(new Error('network error'))
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    await triggerDownload('https://example.com/file.txt')

    expect(openSpy).toHaveBeenCalledWith(
      'https://example.com/file.txt',
      '_blank'
    )
  })

  it('falls back to window.open when response is not ok', async () => {
    vi.mocked(global.fetch).mockResolvedValue({
      ok: false,
      status: 404,
    } as Response)
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    await triggerDownload('https://example.com/file.txt')

    expect(openSpy).toHaveBeenCalledWith(
      'https://example.com/file.txt',
      '_blank'
    )
  })

  it('uses empty filename when content-disposition is missing', async () => {
    const blob = new Blob(['content'])
    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(blob),
      headers: new Headers(),
    } as Response)

    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    vi.spyOn(document.body, 'appendChild').mockImplementation(() =>
      document.createElement('a')
    )
    vi.spyOn(document.body, 'removeChild').mockImplementation(() =>
      document.createElement('a')
    )

    await triggerDownload('https://example.com/file.txt')
  })
})
