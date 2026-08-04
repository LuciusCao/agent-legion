import { useRef, useState } from 'react'
import { Button } from '@mui/material'
import type { InteractionNode } from '../types'
import { RichText } from './RichText'
import styles from './InteractionOverlay.module.css'

type InteractionOption = NonNullable<InteractionNode['options']>[number]

interface InteractionOverlayProps {
  node: InteractionNode | null
  currentSentence: string[]
  onWordClick: (word: string) => void
  onReset: () => void
  onContinue: () => void
}

export function InteractionOverlay({
  node,
  currentSentence,
  onWordClick,
  onReset,
  onContinue,
}: InteractionOverlayProps) {
  const [selectedOptions, setSelectedOptions] = useState<InteractionOption[]>(
    []
  )
  const [draggedOptionId, setDraggedOptionId] = useState<string | null>(null)
  const draggedOptionIdRef = useRef<string | null>(null)

  if (!node) return null

  const type = String(node.type ?? '')
  const options = node.options ?? []
  const isSummary = type === 'interaction_summary' || type === 'video_summary'

  const toggleSummaryOption = (option: InteractionOption) => {
    setSelectedOptions((current) => {
      if (current.some((item) => item.id === option.id)) {
        return current.filter((item) => item.id !== option.id)
      }
      return [...current, option]
    })
  }

  const moveSummaryOptionByOffset = (
    activeOptionId: string,
    offset: number
  ) => {
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

  const reorderSummaryOptionBefore = (targetId: string) => {
    const activeOptionId = draggedOptionIdRef.current ?? draggedOptionId
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

  const reorderSummaryOptionToEnd = () => {
    const activeOptionId = draggedOptionIdRef.current ?? draggedOptionId
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

  if (type === 'example_practice') {
    return (
      <div className={styles.practiceToast}>
        <span className={styles.badge}>例题试做</span>
        <p className={styles.cardTitle}>
          <RichText mode="inline">{node.instruction || '先试做'}</RichText>
        </p>
        {node.hint && (
          <p className={styles.hintText}>
            <RichText mode="inline">{node.hint}</RichText>
          </p>
        )}
        <div className={styles.actionRow}>
          <button
            className={styles.textButton}
            type="button"
            onClick={onContinue}
          >
            跳过
          </button>
          <button
            className={styles.primaryButton}
            type="button"
            onClick={onContinue}
          >
            我已完成，继续
          </button>
        </div>
      </div>
    )
  }

  if (isSummary && options.length > 0) {
    return (
      <div className={styles.interactionOverlay}>
        <div className={styles.summaryPanel}>
          <div className={styles.summaryBody} aria-label="互动小结内容">
            <p className={styles.cardTitle}>
              <RichText mode="inline">
                {node.instruction || '按顺序选择'}
              </RichText>
            </p>
            {node.reference_sentence && (
              <p className={styles.hintText}>
                <RichText mode="inline">{node.reference_sentence}</RichText>
              </p>
            )}
            <div className={styles.optionGrid}>
              {options.map((option) => {
                const selected = selectedOptions.some(
                  (item) => item.id === option.id
                )
                return (
                  <button
                    key={option.id}
                    className={
                      selected
                        ? styles.optionButtonSelected
                        : styles.optionButton
                    }
                    type="button"
                    aria-pressed={selected}
                    onClick={() => toggleSummaryOption(option)}
                  >
                    <RichText mode="inline">{option.text}</RichText>
                  </button>
                )
              })}
            </div>
            <div className={styles.summaryPreview} aria-label="已选排序预览">
              {selectedOptions.map((option, index) => (
                <div
                  key={option.id}
                  className={styles.summaryOrderItem}
                  data-testid="summary-order-item"
                  draggable
                  onDragStart={() => {
                    draggedOptionIdRef.current = option.id
                    setDraggedOptionId(option.id)
                  }}
                  onDragEnter={(event) => event.preventDefault()}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault()
                    reorderSummaryOptionBefore(option.id)
                  }}
                  onDragEnd={() => {
                    draggedOptionIdRef.current = null
                    setDraggedOptionId(null)
                  }}
                >
                  <span className={styles.summaryOrderIndex}>{index + 1}</span>
                  <span className={styles.summaryOrderText}>
                    <RichText mode="inline">{option.text}</RichText>
                  </span>
                  <span className={styles.summaryOrderControls}>
                    <button
                      className={styles.iconButton}
                      type="button"
                      aria-label={`上移 ${option.text}`}
                      disabled={index === 0}
                      onClick={() => moveSummaryOptionByOffset(option.id, -1)}
                    >
                      ↑
                    </button>
                    <button
                      className={styles.iconButton}
                      type="button"
                      aria-label={`下移 ${option.text}`}
                      disabled={index === selectedOptions.length - 1}
                      onClick={() => moveSummaryOptionByOffset(option.id, 1)}
                    >
                      ↓
                    </button>
                  </span>
                </div>
              ))}
              {selectedOptions.length > 1 && (
                <div
                  className={styles.summaryEndDropZone}
                  aria-label="拖到末尾"
                  onDragEnter={(event) => event.preventDefault()}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => {
                    event.preventDefault()
                    reorderSummaryOptionToEnd()
                  }}
                >
                  拖到这里放到末尾
                </div>
              )}
            </div>
          </div>
          <div className={styles.actionRow} aria-label="互动操作">
            <button
              className={styles.textButton}
              type="button"
              onClick={() => setSelectedOptions([])}
            >
              重置
            </button>
            <button
              className={styles.textButton}
              type="button"
              onClick={onContinue}
            >
              跳过
            </button>
            <button
              className={styles.primaryButton}
              type="button"
              onClick={onContinue}
            >
              确认并继续
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (options.length > 0) {
    return (
      <div className={styles.interactionOverlay}>
        <div className={styles.practiceCard}>
          <p className={styles.cardTitle}>
            <RichText mode="inline">{node.instruction || '互动'}</RichText>
          </p>
          {node.reference_sentence && (
            <p className={styles.hintText}>
              <RichText mode="inline">{node.reference_sentence}</RichText>
            </p>
          )}
          <div className={styles.optionGrid}>
            {options.map((opt, i) => (
              <button
                className={styles.optionButton}
                type="button"
                key={i}
                onClick={onContinue}
              >
                <RichText mode="inline">{opt.text}</RichText>
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // Fallback: sentence-building or generic interaction
  const words = node.answer || []
  return (
    <div className={styles.interactionOverlay}>
      <div className={styles.sentenceCard}>
        <p>
          <RichText mode="inline">{node.instruction || '连词成句'}</RichText>
        </p>
        <div className={styles.sentenceBox}>{currentSentence.join(' ')}</div>
        {words.length > 0 && (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {words.map((word, index) => (
              <Button
                key={`${word}-${index}`}
                variant="outlined"
                onClick={() => onWordClick(word)}
              >
                {word}
              </Button>
            ))}
          </div>
        )}
        <div>
          <Button variant="text" onClick={onReset}>
            重置
          </Button>
          <Button variant="contained" onClick={onContinue}>
            确认
          </Button>
        </div>
      </div>
    </div>
  )
}
