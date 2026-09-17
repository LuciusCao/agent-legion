/**
 * previewHostKey 契约（#615 从 PreviewPanelSection 的 hashBundle 提取；
 * codex P2 修复后内容指纹 = 服务端 html_hash，前端不再自算）：
 * jobId 与 bundle 内容共同决定 key——身份变或内容变都换 key（重挂），
 * 同身份同内容跨轮询帧稳定（草稿轮询的对象引用重建不触发无谓重挂）。
 * 内容 → 指纹的映射是服务端契约（preview_panels.bundle_hash = sha256
 * hex，html 与 html_hash 同对象下发），测试内用同算法复算以钉住端到端
 * 语义——尤其覆盖旧 32 位多项式哈希可构造碰撞的 `Aa`/`BB` 场景：
 * sha256 下必分歧，碰撞概率可忽略。复算用 node:crypto 的同步 sha256
 * （jsdom 24 无 crypto.subtle）。
 */
import { describe, it, expect } from 'vitest'
import { createHash } from 'node:crypto'
import { previewHostKey } from './bundleKey'

/** 与服务端 preview_panels.bundle_hash 同算法（sha256 hex）——模拟后端指纹。 */
function serverHtmlHash(html: string): string {
  return createHash('sha256').update(html, 'utf8').digest('hex')
}

describe('previewHostKey', () => {
  it('不同 jobId（同内容）→ 不同 key：桥上下文身份变化必重挂', () => {
    const html = '<!doctype html><html><body>x</body></html>'
    const hash = serverHtmlHash(html)
    expect(previewHostKey('job-1', hash)).not.toBe(
      previewHostKey('job-2', hash)
    )
  })

  it('bundle 内容变化 → 不同 key：内容变化必重挂（codex P2）', () => {
    expect(previewHostKey('job-1', serverHtmlHash('<b>a</b>'))).not.toBe(
      previewHostKey('job-1', serverHtmlHash('<b>b</b>'))
    )
  })

  it('旧 32 位哈希的可构造碰撞对（Aa/BB）→ 不同 key：sha256 指纹必分歧', () => {
    // 31 进制滚动哈希下 'Aa' 与 'BB' 同值（65·31+97 = 66·31+66 = 2112），
    // 相同前后缀中替换即可让旧指纹共享 key、绕过重挂；sha256 无此碰撞。
    expect(previewHostKey('job-1', serverHtmlHash('Aa'))).not.toBe(
      previewHostKey('job-1', serverHtmlHash('BB'))
    )
    // 贴近真实 bundle 的同形场景：相同前后缀、中段 `Aa`↔`BB`。
    const bundle = (mid: string) =>
      `<!doctype html><html><body>panel ${mid} renders</body></html>`
    expect(previewHostKey('job-1', serverHtmlHash(bundle('Aa')))).not.toBe(
      previewHostKey('job-1', serverHtmlHash(bundle('BB')))
    )
  })

  it('同 jobId 同内容 → 稳定 key（轮询重建响应对象不触发重挂）', () => {
    // 两次独立复算（模拟两次轮询各自重建响应对象）：sha256 确定性 → 同 key。
    expect(previewHostKey('job-1', serverHtmlHash('same'))).toBe(
      previewHostKey('job-1', serverHtmlHash('same'))
    )
  })

  it('key 含 jobId：桥上下文身份可读（防指纹与身份脱钩）', () => {
    expect(previewHostKey('job-1', 'x')).toMatch(/^job-1:/)
  })
})
