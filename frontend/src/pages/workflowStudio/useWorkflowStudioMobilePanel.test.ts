import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'

describe('useWorkflowStudioMobilePanel', () => {
  it('defaults to graph panel', () => {
    const { result } = renderHook(() => useWorkflowStudioMobilePanel(null))
    expect(result.current.mobilePanel).toBe('graph')
  })

  it('switches to inspector when a node is selected', () => {
    const { result, rerender } = renderHook(
      ({ selectedNodeKey }: { selectedNodeKey: string | null }) =>
        useWorkflowStudioMobilePanel(selectedNodeKey),
      { initialProps: { selectedNodeKey: null as string | null } }
    )

    rerender({ selectedNodeKey: 'node-a' })
    expect(result.current.mobilePanel).toBe('inspector')
  })
})
