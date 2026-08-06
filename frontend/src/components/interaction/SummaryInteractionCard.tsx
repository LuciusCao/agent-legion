import type { InteractionNode } from '../../types'
import { RichText } from '../RichText'
import styles from '../InteractionOverlay.module.css'
import { useSummaryOrder } from './useSummaryOrder'
import type { InteractionOption } from './useSummaryOrder'

interface SummaryInteractionCardProps {
  node: InteractionNode
  options: InteractionOption[]
  onContinue: () => void
}

export function SummaryInteractionCard({
  node,
  options,
  onContinue,
}: SummaryInteractionCardProps) {
  const {
    selectedOptions,
    toggleOption,
    moveOptionByOffset,
    reorderOptionBefore,
    reorderOptionToEnd,
    beginDrag,
    endDrag,
    reset,
  } = useSummaryOrder()

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
                    selected ? styles.optionButtonSelected : styles.optionButton
                  }
                  type="button"
                  aria-pressed={selected}
                  onClick={() => toggleOption(option)}
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
                onDragStart={() => beginDrag(option.id)}
                onDragEnter={(event) => event.preventDefault()}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault()
                  reorderOptionBefore(option.id)
                }}
                onDragEnd={endDrag}
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
                    onClick={() => moveOptionByOffset(option.id, -1)}
                  >
                    ↑
                  </button>
                  <button
                    className={styles.iconButton}
                    type="button"
                    aria-label={`下移 ${option.text}`}
                    disabled={index === selectedOptions.length - 1}
                    onClick={() => moveOptionByOffset(option.id, 1)}
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
                  reorderOptionToEnd()
                }}
              >
                拖到这里放到末尾
              </div>
            )}
          </div>
        </div>
        <div className={styles.actionRow} aria-label="互动操作">
          <button className={styles.textButton} type="button" onClick={reset}>
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
