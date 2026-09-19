import { describe, expect, it } from 'vitest'
import { workerConsoleUrl } from './workerConsoleUrl'

describe('workerConsoleUrl', () => {
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
