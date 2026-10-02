import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { safeWorkerConsoleAddress } from './workerConsoleAddress'

const cases: { valid: string[]; invalid: string[] } = JSON.parse(
  readFileSync(
    new URL(
      '../../../tests/fixtures/worker-console-urls.json',
      import.meta.url
    ),
    'utf8'
  )
)

describe('console navigation contract shared with startup validation', () => {
  it.each(cases.valid)('preserves valid address %j', (value) => {
    expect(safeWorkerConsoleAddress(value)).toBe(value)
  })
  it.each(cases.invalid)('rejects invalid address %j', (value) => {
    expect(safeWorkerConsoleAddress(value)).toBe('')
  })
})
