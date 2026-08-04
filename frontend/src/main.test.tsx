import { isValidElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { createRoot, renderRoot } = vi.hoisted(() => ({
  createRoot: vi.fn(),
  renderRoot: vi.fn(),
}))

vi.mock('react-dom/client', () => ({ createRoot }))

beforeEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  createRoot.mockReturnValue({ render: renderRoot, unmount: vi.fn() })
  document.body.innerHTML = '<div id="app"></div>'
})

describe('frontend entrypoint', () => {
  it('mounts the React application into the app root', async () => {
    const root = document.getElementById('app')

    await import('./main')

    expect(createRoot).toHaveBeenCalledWith(root)
    expect(renderRoot).toHaveBeenCalledOnce()
    expect(isValidElement(renderRoot.mock.calls[0][0])).toBe(true)
  })

  it('fails clearly when the app root is missing', async () => {
    document.body.innerHTML = ''

    await expect(import('./main')).rejects.toThrow('App root not found')
    expect(createRoot).not.toHaveBeenCalled()
  })
})
