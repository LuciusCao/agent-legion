import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'

// Guard for the hand-maintained `browserTestFiles` list in vite.config.ts
// (issue #205): a .test.ts that needs DOM but was never registered there
// silently lands in the node `logic` project and fails (or worse, passes
// misleadingly) without any pointer to the real fix. The pytest equivalent
// on the backend is tests/test_pytest_postgres_boundaries.py.
//
// This file is pure fs + regex logic and must stay runnable in the node
// `logic` project itself — it must never import DOM-dependent modules.

const FRONTEND_ROOT = resolve(import.meta.dirname, '../..')
const VITE_CONFIG_PATH = join(FRONTEND_ROOT, 'vite.config.ts')

// ---------------------------------------------------------------------------
// browserTestFiles extraction
// ---------------------------------------------------------------------------

function readBrowserTestFiles(): string[] {
  const source = readFileSync(VITE_CONFIG_PATH, 'utf8')
  const match = source.match(/const browserTestFiles = \[([^\]]*)\]/)
  if (!match) {
    throw new Error(
      'browserTestFilesGuard: cannot find `const browserTestFiles = [...]` in vite.config.ts — ' +
        'if the list or its declaration moved, update this guard to follow it.'
    )
  }
  return [...match[1].matchAll(/'([^']+)'/g)].map((entry) => entry[1])
}

// ---------------------------------------------------------------------------
// Static DOM-need detection
// ---------------------------------------------------------------------------
//
// Heuristic signals, ordered from "always true" to "module-level lists":
//
// 1. Importing @testing-library/* — every TL entry point (render, renderHook,
//    screen, userEvent, jest-dom matchers) requires a DOM implementation.
//    Under the node environment the file is not even collected ("No test
//    files found"), which is exactly the silent misrouting this guard exists
//    to catch.
// 2. Direct member access on browser-only globals (document.*, window.*,
//    location.*, navigator.*) — after comment stripping, so a mention in a
//    comment never counts. Optional chaining (window?.open) counts too: an
//    undeclared global still throws ReferenceError in node even under `?.`.
// 3. DOM-only constructors / packages (DOMParser, ResizeObserver,
//    IntersectionObserver, MutationObserver, dompurify, katex).
// 4. Known DOM-infrastructure modules — modules whose DOM need hides behind
//    destructured or aliased access the regexes above cannot see (e.g.
//    `location.protocol` via bare identifier, `import katex from 'katex'`
//    re-exported through another module). This list only ever grows when a
//    real misroute has been observed, keeping the heuristic honest.
//
// Zero-false-positive discipline: signals 1-3 are verified against the whole
// current test suite — every unregistered .test.ts that trips them also fails
// or gets skipped in the node project, and every file that passes in node
// trips them only through a module in NODE_SAFE_GUARDED_MODULES (whose every
// DOM access sits behind `typeof window === 'undefined'` guards).

const TESTING_LIBRARY_IMPORT_RE =
  /['"]@testing-library\/(react|user-event|dom|jest-dom)/
const DOM_MEMBER_ACCESS_RE =
  /\b(document|window|location|navigator)\s*\?\.\s*[A-Za-z_$]|\b(document|window|location|navigator)\s*\.\s*[A-Za-z_$]/
const DOM_CONSTRUCTOR_RE =
  /\bnew\s+(DOMParser|ResizeObserver|IntersectionObserver|MutationObserver)\s*\(/
const DOM_PACKAGE_IMPORT_RE = /['"](dompurify|katex)['"]/

// Modules whose DOM accesses are all behind a node fallback
// (`typeof window === 'undefined'` / `typeof document === 'undefined'`),
// so importing them in a node test is safe. When such a module is hit the
// scan does not flag it and does not traverse deeper: its own imports were
// already vetted when the module grew the guard.
const NODE_SAFE_GUARDED_MODULES = [
  'src/api/requestAuth.ts',
  'src/lib/questionHighlight.ts',
  'src/stores/authStore.ts',
]

// Modules that genuinely require DOM but whose usage evades the member-access
// regexes (bare `location.protocol`, re-exported katex/DOMPurify, ...).
// Verify by running the file in the node project before adding an entry.
const DOM_INFRASTRUCTURE_MODULES = [
  'src/lib/download.ts',
  'src/lib/htmlText.ts',
  'src/lib/latex.ts',
  'src/lib/sanitizeHtml.ts',
  'src/lib/sanitizeHooks.ts',
  'src/stores/agentsStore.ts',
]

// Test files the detector flags but that provably run in the node project.
// Each entry needs a reason — stale entries are reported so they get pruned.
// Format: 'relative/path.test.ts: reason'
const NODE_SAFE_TEST_EXEMPTIONS = [
  'src/lib/questionHighlight.test.ts: questionHighlight.ts falls back to regex entity decoding when window is undefined',
  'src/pages/jobDetail/jobNodeHelpers.test.ts: DagGraph imports are type-only; runtime stays pure',
  'src/pages/workflowStudio/workflowStudioDag.test.ts: DagGraph imports are type-only; runtime stays pure',
  'src/pages/workflowStudio/workflowStudioDagChanges.test.ts: DagGraph imports are type-only; runtime stays pure',
  'src/stores/job/actions/batchAllMatching.test.ts: fixtures.ts only types the WebSocket mock shape',
  'src/stores/job/derivedStateInvariant.test.ts: fixtures.ts only types the WebSocket mock shape',
  'src/stores/jobStore.rerunByFailure.test.ts: fixtures.ts only types the WebSocket mock shape',
  'src/stores/jobStore.test.ts: fixtures.ts only types the WebSocket mock shape',
  'src/stores/settingStore.test.ts: fixtures.ts only types the WebSocket mock shape',
]

// ---------------------------------------------------------------------------
// Import-graph walk
// ---------------------------------------------------------------------------

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ')
}

function parseImportSpecifiers(source: string): string[] {
  const specifiers: string[] = []
  // Static imports/exports are statement-form (line-anchored); dynamic
  // `import('...')` can appear mid-expression (`const m = await import('...')`,
  // `Promise.all([import('...')])`, ...), so it must not be line-anchored —
  // codex review on PR #229: a line-anchored dynamic-import branch silently
  // skips exactly the tests whose DOM need hides behind a lazy import.
  const importRe =
    /(?:^|\n)\s*import\s+(?:type\s+)?(?:[^;'"]*?from\s*)?['"]([^'"]+)['"]|(?:^|\n)\s*export\s+(?:type\s+)?[^;'"]*?from\s+['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]/g
  let match: RegExpExecArray | null
  while ((match = importRe.exec(source))) {
    const specifier = match[1] ?? match[2] ?? match[3]
    if (specifier) specifiers.push(specifier)
  }
  return specifiers
}

function resolveRelativeImport(
  fromFile: string,
  specifier: string
): string | null {
  if (!specifier.startsWith('.')) return null
  const base = resolve(dirname(fromFile), specifier)
  const candidates = [
    `${base}.ts`,
    `${base}.tsx`,
    `${base}.js`,
    `${base}.jsx`,
    `${base}/index.ts`,
    `${base}/index.tsx`,
  ]
  for (const candidate of candidates) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
  }
  return null
}

function isNodeModulePath(path: string): boolean {
  return path.includes('/node_modules/')
}

function collectTestFiles(root: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) files.push(...collectTestFiles(path))
    else if (entry.name.endsWith('.test.ts')) files.push(path)
  }
  return files
}

interface DomDetection {
  needsDom: boolean
  /** Import chain (absolute paths, entry first) leading to the DOM signal. */
  chain: string[]
  /** Relative path of the file whose source trips a DOM signal. */
  evidence: string
  /** Which signal tripped. */
  signal: string
}

function detectDomNeed(entryPath: string): DomDetection | null {
  const visited = new Set<string>()
  const stack: { file: string; chain: string[] }[] = [
    { file: entryPath, chain: [] },
  ]
  while (stack.length > 0) {
    const { file, chain } = stack.pop()!
    if (visited.has(file)) continue
    visited.add(file)
    const relativePath = relative(FRONTEND_ROOT, file)
    const source = stripComments(readFileSync(file, 'utf8'))

    if (TESTING_LIBRARY_IMPORT_RE.test(source)) {
      return {
        needsDom: true,
        chain: [...chain, file],
        evidence: relativePath,
        signal:
          "imports '@testing-library/*' (render/renderHook require a DOM)",
      }
    }
    if (DOM_CONSTRUCTOR_RE.test(source) || DOM_PACKAGE_IMPORT_RE.test(source)) {
      return {
        needsDom: true,
        chain: [...chain, file],
        evidence: relativePath,
        signal:
          'uses a DOM-only constructor or package (DOMParser / ResizeObserver / dompurify / katex)',
      }
    }
    if (DOM_MEMBER_ACCESS_RE.test(source)) {
      if (NODE_SAFE_GUARDED_MODULES.includes(relativePath)) continue
      return {
        needsDom: true,
        chain: [...chain, file],
        evidence: relativePath,
        signal: 'accesses document/window/location/navigator members',
      }
    }
    if (DOM_INFRASTRUCTURE_MODULES.includes(relativePath)) {
      return {
        needsDom: true,
        chain: [...chain, file],
        evidence: relativePath,
        signal:
          'known DOM-infrastructure module (listed in DOM_INFRASTRUCTURE_MODULES)',
      }
    }

    for (const specifier of parseImportSpecifiers(source)) {
      const resolved = resolveRelativeImport(file, specifier)
      if (resolved && !isNodeModulePath(resolved)) {
        stack.push({ file: resolved, chain: [...chain, file] })
      }
    }
  }
  return null
}

// ---------------------------------------------------------------------------
// Guard assertions
// ---------------------------------------------------------------------------

function parseExemptions(): Map<string, string> {
  const exemptions = new Map<string, string>()
  for (const entry of NODE_SAFE_TEST_EXEMPTIONS) {
    const separator = entry.indexOf(':')
    if (separator === -1) {
      throw new Error(
        `browserTestFilesGuard: exemption entry must be '<path>: <reason>' — got ${entry}`
      )
    }
    exemptions.set(
      entry.slice(0, separator).trim(),
      entry.slice(separator + 1).trim()
    )
  }
  return exemptions
}

function formatChain(chain: string[]): string {
  return chain.map((path) => relative(FRONTEND_ROOT, path)).join(' -> ')
}

describe('browserTestFiles guard (vite.config.ts)', () => {
  it('lists every .test.ts that statically needs DOM', () => {
    const registered = new Set(readBrowserTestFiles())
    const exemptions = parseExemptions()
    const testFiles = collectTestFiles(join(FRONTEND_ROOT, 'src')).sort()

    const unregistered: string[] = []
    for (const testFile of testFiles) {
      const relativePath = relative(FRONTEND_ROOT, testFile)
      if (registered.has(relativePath)) continue
      const detection = detectDomNeed(testFile)
      if (!detection) continue
      if (exemptions.has(relativePath)) continue
      unregistered.push(
        `  ${relativePath}\n    evidence: ${detection.evidence} ${detection.signal}\n` +
          `    chain: ${formatChain(detection.chain)}`
      )
    }

    expect(
      unregistered,
      `Found .test.ts files that need DOM but are NOT registered in browserTestFiles (vite.config.ts).\n` +
        `Without registration vitest silently runs them in the node 'logic' project where they\n` +
        `fail or get skipped with no pointer to the fix. Either add the path to browserTestFiles\n` +
        `in frontend/vite.config.ts, or — if the file genuinely runs in node — add it to\n` +
        `NODE_SAFE_TEST_EXEMPTIONS in src/lib/browserTestFilesGuard.test.ts with a reason.\n\n` +
        unregistered.join('\n\n') +
        '\n'
    ).toEqual([])
  })

  it('registers only files that exist (no dead entries)', () => {
    const stale = readBrowserTestFiles().filter(
      (entry) => !existsSync(join(FRONTEND_ROOT, entry))
    )
    // Mirrors the backend guard test_postgres_inventory_entries_reference_
    // existing_files: a deleted/renamed test leaves a dead entry nothing
    // notices, and it can hide future registration misses.
    expect(
      stale,
      `browserTestFiles in vite.config.ts references files that do not exist: ${stale.join(', ')}`
    ).toEqual([])
  })

  it('registers only .test.ts files routed to the node project', () => {
    // browserTestFiles only has an effect for files matching the logic
    // project's `src/**/*.test.ts` include pattern. A .test.tsx entry or a
    // path outside src/ is dead configuration (the component project already
    // includes every .test.tsx).
    const misplaced = readBrowserTestFiles().filter(
      (entry) =>
        !(
          entry.startsWith('src/') &&
          entry.endsWith('.test.ts') &&
          !entry.endsWith('.test.tsx')
        )
    )
    expect(
      misplaced,
      `browserTestFiles entries that cannot match the logic project's include pattern ` +
        `(src/**/*.test.ts): ${misplaced.join(', ')}`
    ).toEqual([])
  })

  it('keeps exemption entries pointing at real, unregistered files', () => {
    const registered = new Set(readBrowserTestFiles())
    const stale: string[] = []
    for (const [path, reason] of parseExemptions()) {
      if (!existsSync(join(FRONTEND_ROOT, path))) {
        stale.push(`unknown file: ${path}`)
        continue
      }
      if (registered.has(path)) {
        stale.push(`already registered in browserTestFiles: ${path}`)
        continue
      }
      const detection = detectDomNeed(join(FRONTEND_ROOT, path))
      if (!detection) {
        stale.push(
          `no longer flagged by the detector — the entry (and its reason) is stale: ${path}`
        )
        continue
      }
      if (!reason) {
        stale.push(`missing reason: ${path}`)
      }
    }
    expect(
      stale,
      `Stale NODE_SAFE_TEST_EXEMPTIONS entries in src/lib/browserTestFilesGuard.test.ts:\n  ` +
        stale.join('\n  ')
    ).toEqual([])
  })

  it('keeps the node-safe module lists free of overlap and pointed at real files', () => {
    const overlap = NODE_SAFE_GUARDED_MODULES.filter((path) =>
      DOM_INFRASTRUCTURE_MODULES.includes(path)
    )
    expect(overlap, 'module listed as both node-safe and DOM-only').toEqual([])

    for (const path of [
      ...NODE_SAFE_GUARDED_MODULES,
      ...DOM_INFRASTRUCTURE_MODULES,
    ]) {
      expect(
        existsSync(join(FRONTEND_ROOT, path)),
        `module list references a missing file: ${path}`
      ).toBe(true)
    }
  })
})
