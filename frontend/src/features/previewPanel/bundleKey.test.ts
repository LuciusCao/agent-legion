/**
 * previewHostKey 契约（#615 从 PreviewPanelSection 的 hashBundle 提取）：
 * jobId 与 bundle 内容共同决定 key——身份变或内容变都换 key（重挂），
 * 同身份同内容跨轮询帧稳定（草稿轮询的对象引用重建不触发无谓重挂）。
 * 授权判定不用它（比对服务端 html_hash），这里只钉重挂语义。
 */
import { describe, it, expect } from 'vitest'
import { previewHostKey } from './bundleKey'

describe('previewHostKey', () => {
  it('不同 jobId（同内容）→ 不同 key：桥上下文身份变化必重挂', () => {
    const html = '<!doctype html><html><body>x</body></html>'
    expect(previewHostKey('job-1', html)).not.toBe(
      previewHostKey('job-2', html)
    )
  })

  it('bundle 内容变化 → 不同 key：内容变化必重挂（codex P2）', () => {
    expect(previewHostKey('job-1', '<b>a</b>')).not.toBe(
      previewHostKey('job-1', '<b>b</b>')
    )
  })

  it('同 jobId 同内容 → 稳定 key（轮询重建响应对象不触发重挂）', () => {
    expect(previewHostKey('job-1', 'same')).toBe(
      previewHostKey('job-1', 'same')
    )
  })

  it('key 含 jobId：桥上下文身份可读（防指纹与身份脱钩）', () => {
    expect(previewHostKey('job-1', 'x')).toMatch(/^job-1:/)
  })
})
