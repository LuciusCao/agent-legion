import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// #1180 UI 验收返工：版本菜单每行 hash 右对齐。jsdom 测不了 flex 布局，
// 按既有模式（DagNode.test.tsx）做源断言：.hash 规则必须具备
// margin-left: auto（把 hash 顶到 flex 行行尾，各行右边沿对齐），
// 防止被改回固定左 margin 后 ACTIVE/ARCHIVED 行 hash 错开回归。
describe('WorkflowRevisionSelect styles', () => {
  it('pins the hash column to the row end so all rows align on the right edge', () => {
    const css = readFileSync(
      resolve(__dirname, 'WorkflowRevisionSelect.module.css'),
      'utf-8'
    )
    const hashRule = css.match(/\.hash\s*\{([^}]*)\}/)
    expect(hashRule).not.toBeNull()
    expect(hashRule![1]).toContain('margin-left: auto')
  })
})
