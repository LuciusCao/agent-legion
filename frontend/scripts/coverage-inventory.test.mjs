import assert from 'node:assert/strict'
import test from 'node:test'
import {
  findMissingCoverage,
  shouldTrackSource,
} from './coverage-inventory.mjs'

test('tracks production TypeScript entrypoints and type-only modules', () => {
  assert.equal(shouldTrackSource('src/main.tsx'), true)
  assert.equal(shouldTrackSource('src/pages/LoginPage.tsx'), true)
  assert.equal(shouldTrackSource('src/types/jobTypes.ts'), true)
})

test('excludes generated code, declarations, tests, and test support', () => {
  const excluded = [
    'src/generated/api.ts',
    'src/global.d.ts',
    'src/lib/jobDag.test.ts',
    'src/testing/fixtures.ts',
    'src/test-setup.ts',
    'src/test-setup-console.ts',
  ]
  for (const filePath of excluded) {
    assert.equal(shouldTrackSource(filePath), false, filePath)
  }
})

test('reports production files absent from the coverage payload', () => {
  const expected = ['src/App.tsx', 'src/main.tsx']
  const covered = ['src/App.tsx']
  assert.deepEqual(findMissingCoverage(expected, covered), ['src/main.tsx'])
})
