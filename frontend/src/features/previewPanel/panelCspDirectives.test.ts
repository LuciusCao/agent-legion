/**
 * panelCspDirectives 直接单测（#989）：CSP3 解析规则与 Chromium 实测出入处
 * （逗号拆策略、\v 空白、非法字符整条丢弃但占名）。
 */
import { describe, expect, it } from 'vitest'
import { allowsInlineScript, scriptElemPolicies } from './panelCspDirectives'

const N = "'nonce-p'"
const U = "'unsafe-inline'"

describe('scriptElemPolicies', () => {
  it.each([
    ['无脚本指令', `style-src ${N}; img-src x`, [null]],
    ['script-src', `script-src ${N}`, [[N]]],
    ['script-src-elem 优先', `script-src ${U}; script-src-elem ${N}`, [[N]]],
    ['default-src 回退', `default-src ${N}; style-src ${U}`, [[N]]],
    ['空指令 = none，不再回退', `script-src-elem; script-src ${U}`, [[]]],
    ['重复指令首项生效', `script-src ${U}; script-src ${N}`, [[U]]],
    ['指令名 ASCII 小写后判重', `SCRIPT-SRC ${U}; script-src ${N}`, [[U]]],
    ['空 token 与多余分号', `;; script-src ${N} ;`, [[N]]],
    ['\\v 为空白', `\vscript-src\v${N}\v${U}\v`, [[N, U]]],
    ['\\f 为空白', `script-src\f${N}`, [[N]]],
    ['NBSP 不是空白（指令名不识别）', `script-src\u00a0${N}`, [null]],
    ['逗号拆成多条策略', `script-src ${N}, script-src ${U}`, [[N], [U]]],
    ['逗号后空策略', `script-src ${N}, `, [[N], null]],
    ['非 ASCII 值：整条丢弃', `script-src ${N} é`, [null]],
    ['控制字符值：整条丢弃', `script-src ${N} \x01`, [null]],
    ['DEL 值：整条丢弃', `script-src ${N} \x7f`, [null]],
    ['丢弃后回退下一级', `script-src-elem ${U} é; script-src ${N}`, [[N]]],
    [
      '丢弃的指令占名',
      `script-src-elem ${U} é; script-src-elem ${N}; script-src ${U}`,
      [[U]],
    ],
  ])('%s', (_name, content, expected) => {
    expect(scriptElemPolicies(content)).toEqual(expected)
  })
})

describe('allowsInlineScript', () => {
  it.each([
    ['无生效指令', null, 'x', true],
    ['nonce 匹配', [N], 'p', true],
    ['nonce 不匹配', [N], 'q', false],
    ['nonce- 前缀大小写不敏感', ["'NONCE-p'"], 'p', true],
    ['nonce 值大小写敏感', ["'nonce-P'"], 'p', false],
    ["'unsafe-inline' 放行任意", [U], 'x', true],
    ["'UNSAFE-INLINE' 关键字大小写不敏感", ["'UNSAFE-INLINE'"], 'x', true],
    ["nonce 使 'unsafe-inline' 失效", [U, N], 'x', false],
    ["hash 使 'unsafe-inline' 失效", [U, "'sha256-AAAA'"], 'x', false],
    [
      "'strict-dynamic' 使 'unsafe-inline' 失效",
      [U, "'strict-dynamic'"],
      'x',
      false,
    ],
    ['非法 nonce 语法不算 nonce 源', [U, "'nonce-a=b'"], 'x', true],
    ['非法 nonce 语法不匹配', ["'nonce-a=b'"], 'a=b', false],
    ['空列表 = none', [], 'p', false],
  ] as const)('%s', (_name, sources, nonce, expected) => {
    expect(
      allowsInlineScript(sources === null ? null : [...sources], nonce)
    ).toBe(expected)
  })
})
