import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { error as logError, log } from 'node:console'

const frontendRoot = path.resolve(fileURLToPath(import.meta.url), '../..')
const sourceRoot = path.join(frontendRoot, 'src')

function normalize(filePath) {
  return filePath.split(path.sep).join('/')
}

export function shouldTrackSource(filePath) {
  const normalized = normalize(filePath)
  return (
    /^src\/.*\.(ts|tsx)$/.test(normalized) &&
    !normalized.endsWith('.d.ts') &&
    !/\.test\.(ts|tsx)$/.test(normalized) &&
    !normalized.startsWith('src/generated/') &&
    !normalized.startsWith('src/testing/') &&
    !/^src\/test-setup.*\.ts$/.test(normalized)
  )
}

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const nested = await Promise.all(
    entries.map((entry) => {
      const entryPath = path.join(directory, entry.name)
      return entry.isDirectory() ? walk(entryPath) : [entryPath]
    })
  )
  return nested.flat()
}

export function findMissingCoverage(expectedFiles, coverageFiles) {
  const covered = new Set(
    coverageFiles.map((filePath) => {
      const absolutePath = path.isAbsolute(filePath)
        ? filePath
        : path.resolve(frontendRoot, filePath)
      return normalize(path.relative(frontendRoot, absolutePath))
    })
  )
  return expectedFiles.filter((filePath) => !covered.has(filePath))
}

export async function checkCoverageInventory(
  coveragePath = path.join(frontendRoot, 'coverage/coverage-final.json')
) {
  const sourceFiles = (await walk(sourceRoot))
    .map((filePath) => normalize(path.relative(frontendRoot, filePath)))
    .filter(shouldTrackSource)
    .sort()
  const coverage = JSON.parse(await readFile(coveragePath, 'utf8'))
  const missing = findMissingCoverage(sourceFiles, Object.keys(coverage))
  if (missing.length > 0) {
    throw new Error(
      `Coverage inventory is missing ${missing.length} production source file(s):\n${missing.join('\n')}`
    )
  }
  return sourceFiles.length
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    const trackedCount = await checkCoverageInventory()
    log(`Coverage inventory includes ${trackedCount} production source files.`)
  } catch (error) {
    logError(error instanceof Error ? error.message : String(error))
    process.exitCode = 1
  }
}
