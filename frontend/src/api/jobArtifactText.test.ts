import { describe, it, expect } from 'vitest'
import { jobArtifactTextPreviewOf } from './jobArtifactText'

const LIMIT = 10

describe('jobArtifactTextPreviewOf', () => {
  it('206 Content-Range 给出总数时按总数判定截断', () => {
    const result = jobArtifactTextPreviewOf(
      'x'.repeat(11),
      'bytes 0-10/1000',
      LIMIT
    )
    expect(result.truncated).toBe(true)
    expect(result.total).toBe(1000)
    expect(result.content).toBe('x'.repeat(10))
  })

  it('206 且总数不超过读取窗口时不算截断', () => {
    const result = jobArtifactTextPreviewOf('x'.repeat(5), 'bytes 0-4/5', LIMIT)
    expect(result.truncated).toBe(false)
    expect(result.total).toBe(5)
    expect(result.content).toBe('x'.repeat(5))
  })

  it('200 全文（无 Content-Range）按已读长度兜底截断', () => {
    const result = jobArtifactTextPreviewOf('x'.repeat(30), null, LIMIT)
    expect(result.truncated).toBe(true)
    expect(result.total).toBe(30)
    expect(result.content).toBe('x'.repeat(10))
  })

  it('200 短文本不截断', () => {
    const result = jobArtifactTextPreviewOf('abc', null, LIMIT)
    expect(result.truncated).toBe(false)
    expect(result.content).toBe('abc')
  })
})
