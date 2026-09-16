import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useWorkflowStudioDraft } from './useWorkflowStudioDraft'

// 基线同步（外部基线变化 preserve / 干净跟随 / 发布后强制 reset / 陈旧标记）
// 用例：自 useWorkflowStudio.draft.test.ts 按测试文件体积纪律拆出（#670 codex
// P1，该文件超 800 行），用例零改动迁移。
describe('useWorkflowStudioDraft baseline sync', () => {
  it('preserves a dirty draft when the baseline changes externally', () => {
    const fetchDetail = vi.fn()
    const { result, rerender } = renderHook(
      ({ originalYaml }: { originalYaml: string }) =>
        useWorkflowStudioDraft('ws1', originalYaml, null, null, fetchDetail),
      { initialProps: { originalYaml: 'key: demo\nlabel: v1\n' } }
    )

    // 初始装载：草稿跟随基线。
    expect(result.current.draftYaml).toBe('key: demo\nlabel: v1\n')
    act(() => result.current.setDraftYaml('key: demo\nlabel: my edits\n'))
    expect(result.current.dirty).toBe(true)

    // 外部（他人/他 tab）发布使基线前进：用户草稿保留，打 preserved 标记。
    rerender({ originalYaml: 'key: demo\nlabel: v2\n' })

    expect(result.current.draftYaml).toBe('key: demo\nlabel: my edits\n')
    expect(result.current.hasPreservedDraft).toBe(true)
  })

  it('resets to the new baseline when the draft is clean or matches it', () => {
    const fetchDetail = vi.fn()
    const { result, rerender } = renderHook(
      ({ originalYaml }: { originalYaml: string }) =>
        useWorkflowStudioDraft('ws1', originalYaml, null, null, fetchDetail),
      { initialProps: { originalYaml: 'key: demo\nlabel: v1\n' } }
    )

    // 干净草稿：跟随新基线。
    rerender({ originalYaml: 'key: demo\nlabel: v2\n' })
    expect(result.current.draftYaml).toBe('key: demo\nlabel: v2\n')
    expect(result.current.hasPreservedDraft).toBe(false)

    // 自己 publish 成功：草稿与新基线一致，常规 reset 不误标 preserved。
    rerender({ originalYaml: 'key: demo\nlabel: v3\n' })
    act(() => result.current.setDraftYaml('key: demo\nlabel: v3\n'))
    rerender({ originalYaml: 'key: demo\nlabel: v3\n' })
    expect(result.current.hasPreservedDraft).toBe(false)
  })

  it('force-resets the draft to the canonical baseline after own publish (#666)', () => {
    // 发布存库的 revision YAML 是 canonical 重建（丢注释、重排 key、补默认
    // 值），与草稿原文纯文本不等：发布后 reload 拉回的基线不得触发
    // preserve（否则 dirty 永真、一直显示未发布）。
    const fetchDetail = vi.fn()
    const { result, rerender } = renderHook(
      ({ originalYaml }: { originalYaml: string }) =>
        useWorkflowStudioDraft('ws1', originalYaml, null, null, fetchDetail),
      { initialProps: { originalYaml: 'key: demo\nlabel: v1\n' } }
    )

    const publishedYaml =
      '# 草稿注释\nkey: demo\nlabel: v2\nnodes:\n  a:\n    capability: cap_a\n'
    act(() => result.current.setDraftYaml(publishedYaml))
    expect(result.current.dirty).toBe(true)

    // publishDraft 成功分支：先登记发布原文，reload 随后拉回 canonical 基线。
    act(() => result.current.markDraftPublished(publishedYaml))
    const canonicalYaml =
      'key: demo\nlabel: v2\nschema_version: 66\nnodes:\n  a:\n    capability: cap_a\n    type: code\n'
    rerender({ originalYaml: canonicalYaml })

    expect(result.current.draftYaml).toBe(canonicalYaml)
    expect(result.current.hasPreservedDraft).toBe(false)
    expect(result.current.dirty).toBe(false)
  })

  it('keeps the draft when the user edits again before the post-publish baseline arrives', () => {
    // reload 在途期间用户又改了草稿（≠ 发布文本）：不强制 reset，回落常规
    // preserve——新编辑不能静默丢失。
    const fetchDetail = vi.fn()
    const { result, rerender } = renderHook(
      ({ originalYaml }: { originalYaml: string }) =>
        useWorkflowStudioDraft('ws1', originalYaml, null, null, fetchDetail),
      { initialProps: { originalYaml: 'key: demo\nlabel: v1\n' } }
    )

    const publishedYaml = 'key: demo\nlabel: v2\n'
    act(() => result.current.setDraftYaml(publishedYaml))
    act(() => result.current.markDraftPublished(publishedYaml))
    act(() => result.current.setDraftYaml('key: demo\nlabel: v2 plus more\n'))

    rerender({ originalYaml: 'key: demo\nlabel: v2 canonical\n' })

    expect(result.current.draftYaml).toBe('key: demo\nlabel: v2 plus more\n')
    expect(result.current.hasPreservedDraft).toBe(true)
  })

  it('ignores a stale publish mark when the baseline changes much later', () => {
    // 发布后 reload 未带来基线变化（失败/无新 revision），用户继续编辑使
    // 草稿离开发布文本：标记失效，之后的无关基线变化不误强制 reset。
    const fetchDetail = vi.fn()
    const { result, rerender } = renderHook(
      ({ originalYaml }: { originalYaml: string }) =>
        useWorkflowStudioDraft('ws1', originalYaml, null, null, fetchDetail),
      { initialProps: { originalYaml: 'key: demo\nlabel: v1\n' } }
    )

    act(() => result.current.setDraftYaml('key: demo\nlabel: v2\n'))
    act(() => result.current.markDraftPublished('key: demo\nlabel: v2\n'))
    act(() => result.current.setDraftYaml('key: demo\nlabel: my edits\n'))

    rerender({ originalYaml: 'key: demo\nlabel: external\n' })

    expect(result.current.draftYaml).toBe('key: demo\nlabel: my edits\n')
    expect(result.current.hasPreservedDraft).toBe(true)
  })
})
