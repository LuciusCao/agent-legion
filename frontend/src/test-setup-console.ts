import { afterEach, beforeEach, vi } from 'vitest'

let unexpectedConsoleErrors: unknown[][] = []
let expectedConsoleErrors: RegExp[] = []
let unexpectedConsoleWarnings: unknown[][] = []
let expectedConsoleWarnings: RegExp[] = []

export function expectConsoleError(pattern: RegExp) {
  expectedConsoleErrors.push(pattern)
}

export function expectConsoleWarning(pattern: RegExp) {
  expectedConsoleWarnings.push(pattern)
}

beforeEach(() => {
  unexpectedConsoleErrors = []
  expectedConsoleErrors = []
  unexpectedConsoleWarnings = []
  expectedConsoleWarnings = []
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    unexpectedConsoleErrors.push(args)
  })
  vi.spyOn(console, 'warn').mockImplementation((...args: unknown[]) => {
    unexpectedConsoleWarnings.push(args)
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  const unmatchedErrors = unexpectedConsoleErrors
    .map((args) => args.map(String).join(' '))
    .filter(
      (message) =>
        !expectedConsoleErrors.some((pattern) => pattern.test(message))
    )
  const unmatchedWarnings = unexpectedConsoleWarnings
    .map((args) => args.map(String).join(' '))
    .filter(
      (message) =>
        !expectedConsoleWarnings.some((pattern) => pattern.test(message))
    )
  const unexpectedMessages = [
    ...unmatchedErrors.map((message) => `console.error: ${message}`),
    ...unmatchedWarnings.map((message) => `console.warn: ${message}`),
  ]
  if (unexpectedMessages.length === 0) return
  throw new Error(
    `Unexpected console output during test:\n${unexpectedMessages.join('\n')}`
  )
})
