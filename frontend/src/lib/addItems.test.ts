import { afterEach, describe, expect, it, vi } from 'vitest'
import { webcrypto } from 'node:crypto'

import {
  computeFileSha256,
  fileTypeGroup,
  formatBytes,
  parseRefIds,
  runWithConcurrency,
  splitBundleRelativePath,
  uploadMaterialFile,
} from './addItems'
import { completeMaterial, presignMaterial } from '../api/materialsApi'

vi.mock('../api/materialsApi', () => ({
  presignMaterial: vi.fn(),
  completeMaterial: vi.fn(),
}))

const mockPresign = vi.mocked(presignMaterial)
const mockComplete = vi.mocked(completeMaterial)

// jsdom 环境的 crypto 可能没有 subtle，退回 Node webcrypto。
if (!globalThis.crypto?.subtle) {
  vi.stubGlobal('crypto', webcrypto)
}

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  vi.clearAllMocks()
})

describe('parseRefIds', () => {
  it('trims lines, drops empties and dedupes in order', () => {
    expect(parseRefIds(' q1 \n\nq2\nq1\n  \nq3')).toEqual(['q1', 'q2', 'q3'])
    expect(parseRefIds('')).toEqual([])
  })
})

describe('splitBundleRelativePath', () => {
  it('splits the root folder from the member path', () => {
    expect(splitBundleRelativePath('root/sub/a.txt')).toEqual({
      root: 'root',
      memberPath: 'sub/a.txt',
    })
    expect(splitBundleRelativePath('root/a.txt')).toEqual({
      root: 'root',
      memberPath: 'a.txt',
    })
  })

  it('returns an empty root when there is no folder segment', () => {
    expect(splitBundleRelativePath('a.txt')).toEqual({
      root: '',
      memberPath: 'a.txt',
    })
  })

  it('keeps literal backslashes in POSIX filenames untouched', () => {
    // webkitRelativePath 恒以 `/` 分隔（Windows 上亦然）；字面反斜杠是
    // 合法文件名字符，不得被归一化成路径段——含 `\` 的成员路径由后端
    // 校验 fail-closed 拒绝，而不是静默改写。
    expect(splitBundleRelativePath('root/a\\b.txt')).toEqual({
      root: 'root',
      memberPath: 'a\\b.txt',
    })
  })
})

describe('fileTypeGroup', () => {
  it('groups by mime main type, then extension', () => {
    expect(fileTypeGroup('a.png', 'image/png')).toBe('图片')
    expect(fileTypeGroup('a.mp4', 'video/mp4')).toBe('视频')
    expect(fileTypeGroup('a.pdf', 'application/pdf')).toBe('PDF')
    expect(fileTypeGroup('a.txt', 'text/plain')).toBe('文本')
    expect(fileTypeGroup('data.csv', '')).toBe('.csv')
    expect(fileTypeGroup('README', '')).toBe('其他')
  })
})

describe('formatBytes', () => {
  it('formats human readable sizes', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5 MB')
  })
})

describe('computeFileSha256', () => {
  it('hashes small files and skips oversized ones', async () => {
    const small = new File(['hello'], 'a.txt')
    const hash = await computeFileSha256(small)
    // sha256('hello')
    expect(hash).toBe(
      '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    )
    const big = new File(['x'], 'b.bin')
    Object.defineProperty(big, 'size', { value: 65 * 1024 * 1024 })
    expect(await computeFileSha256(big)).toBeNull()
  })
})

describe('runWithConcurrency', () => {
  it('never exceeds the concurrency limit and processes every item', async () => {
    let active = 0
    let maxActive = 0
    const processed: number[] = []
    const items = Array.from({ length: 10 }, (_, i) => i)
    await runWithConcurrency(items, 3, async (item) => {
      active += 1
      maxActive = Math.max(maxActive, active)
      await new Promise((resolve) => setTimeout(resolve, 5))
      processed.push(item)
      active -= 1
    })
    expect(maxActive).toBeLessThanOrEqual(3)
    expect(processed.sort()).toEqual(items)
  })
})

describe('uploadMaterialFile', () => {
  const file = new File(['hello'], 'a.txt', { type: 'text/plain' })

  it('runs presign → PUT → complete and returns the material id', async () => {
    mockPresign.mockResolvedValue({
      material: { id: 'm1' },
      upload_url: 'https://s3.example/put',
      upload_expires_in_seconds: 900,
      deduplicated: false,
    } as never)
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200 } as Response)
    global.fetch = fetchMock
    mockComplete.mockResolvedValue({ material: { id: 'm1' } } as never)

    const result = await uploadMaterialFile('ws1', file, 'a.txt')

    expect(result).toEqual({ materialId: 'm1', deduplicated: false })
    expect(mockPresign).toHaveBeenCalledWith(
      'ws1',
      expect.objectContaining({
        filename: 'a.txt',
        size_bytes: file.size,
        content_type: 'text/plain',
        content_hash:
          '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',
      })
    )
    expect(fetchMock).toHaveBeenCalledWith('https://s3.example/put', {
      method: 'PUT',
      body: file,
    })
    expect(mockComplete).toHaveBeenCalledWith('ws1', 'm1')
  })

  it('skips the PUT when the material is deduplicated', async () => {
    mockPresign.mockResolvedValue({
      material: { id: 'm1' },
      upload_url: null,
      upload_expires_in_seconds: 900,
      deduplicated: true,
    } as never)
    const fetchMock = vi.fn()
    global.fetch = fetchMock

    const result = await uploadMaterialFile('ws1', file, 'a.txt')

    expect(result).toEqual({ materialId: 'm1', deduplicated: true })
    expect(fetchMock).not.toHaveBeenCalled()
    expect(mockComplete).not.toHaveBeenCalled()
  })

  it('throws when the direct PUT fails', async () => {
    mockPresign.mockResolvedValue({
      material: { id: 'm1' },
      upload_url: 'https://s3.example/put',
      upload_expires_in_seconds: 900,
      deduplicated: false,
    } as never)
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 403 } as Response)

    await expect(uploadMaterialFile('ws1', file, 'a.txt')).rejects.toThrow(
      '直传失败 (HTTP 403)'
    )
    expect(mockComplete).not.toHaveBeenCalled()
  })
})
