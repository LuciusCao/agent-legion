import { describe, expect, it } from 'vitest'
import { workerConsoleUrl } from './workerConsoleUrl'

describe('workerConsoleUrl', () => {
  it.each([
    'http://',
    'https://worker.example:65536',
    'https://user:secret@worker.example/',
    'https://worker.example\\other',
    'https://bad host/',
  ])('rejects malformed self-reported address %j', (console_url) => {
    expect(workerConsoleUrl({ labels: { console_url } })).toBe('')
  })

  it('trims for navigation without changing the reported label', () => {
    const labels = Object.freeze({ console_url: ' https://worker.example/ ' })
    expect(workerConsoleUrl({ labels })).toBe('https://worker.example/')
    expect(labels.console_url).toBe(' https://worker.example/ ')
  })

  it('reads the self-reported console address from the reserved label', () => {
    expect(
      workerConsoleUrl({ labels: { console_url: 'http://10.0.0.8:8787' } })
    ).toBe('http://10.0.0.8:8787')
    expect(
      workerConsoleUrl({ labels: { console_url: ' https://w.example/ ' } })
    ).toBe('https://w.example/')
  })

  it('returns empty for older workers and non-http values', () => {
    expect(workerConsoleUrl({ labels: {} })).toBe('')
    expect(workerConsoleUrl({ labels: { site: 'office' } })).toBe('')
    expect(workerConsoleUrl({ labels: { console_url: 'javascript:1' } })).toBe(
      ''
    )
    expect(workerConsoleUrl({ labels: { console_url: 'worker-a:8787' } })).toBe(
      ''
    )
  })
})
