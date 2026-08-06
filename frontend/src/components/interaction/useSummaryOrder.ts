import { useRef, useState } from 'react'
import type { InteractionNode } from '../../types'

export type InteractionOption = NonNullable<InteractionNode['options']>[number]

/**
 * Selection + drag ordering state for the summary interaction: options are
 * toggled into an ordered list, reordered by buttons or HTML5 drag & drop.
 */
export function useSummaryOrder() {
  const [selectedOptions, setSelectedOptions] = useState<InteractionOption[]>(
    []
  )
  const [draggedOptionId, setDraggedOptionId] = useState<string | null>(null)
  const draggedOptionIdRef = useRef<string | null>(null)

  const toggleOption = (option: InteractionOption) => {
    setSelectedOptions((current) => {
      if (current.some((item) => item.id === option.id)) {
        return current.filter((item) => item.id !== option.id)
      }
      return [...current, option]
    })
  }

  const moveOptionByOffset = (activeOptionId: string, offset: number) => {
    setSelectedOptions((current) => {
      const fromIndex = current.findIndex((item) => item.id === activeOptionId)
      const targetIndex = fromIndex + offset
      if (fromIndex < 0 || targetIndex < 0 || targetIndex >= current.length)
        return current

      const next = [...current]
      const [moved] = next.splice(fromIndex, 1)
      next.splice(targetIndex, 0, moved)
      return next
    })
  }

  const activeDragId = () => draggedOptionIdRef.current ?? draggedOptionId

  const reorderOptionBefore = (targetId: string) => {
    const activeOptionId = activeDragId()
    if (!activeOptionId || activeOptionId === targetId) return

    const activeId = activeOptionId
    setSelectedOptions((current) => {
      const fromIndex = current.findIndex((item) => item.id === activeId)
      const toIndex = current.findIndex((item) => item.id === targetId)
      if (fromIndex < 0 || toIndex < 0) return current

      const insertionIndex = fromIndex < toIndex ? toIndex - 1 : toIndex
      const next = [...current]
      const [moved] = next.splice(fromIndex, 1)
      next.splice(insertionIndex, 0, moved)
      return next
    })
  }

  const reorderOptionToEnd = () => {
    const activeOptionId = activeDragId()
    if (!activeOptionId) return

    setSelectedOptions((current) => {
      const fromIndex = current.findIndex((item) => item.id === activeOptionId)
      if (fromIndex < 0 || fromIndex === current.length - 1) return current

      const next = [...current]
      const [moved] = next.splice(fromIndex, 1)
      next.push(moved)
      return next
    })
  }

  const beginDrag = (optionId: string) => {
    draggedOptionIdRef.current = optionId
    setDraggedOptionId(optionId)
  }

  const endDrag = () => {
    draggedOptionIdRef.current = null
    setDraggedOptionId(null)
  }

  const reset = () => setSelectedOptions([])

  return {
    selectedOptions,
    toggleOption,
    moveOptionByOffset,
    reorderOptionBefore,
    reorderOptionToEnd,
    beginDrag,
    endDrag,
    reset,
  }
}
