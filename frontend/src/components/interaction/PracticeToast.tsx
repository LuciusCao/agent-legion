import type { InteractionNode } from '../../types'
import { RichText } from '../RichText'
import styles from '../InteractionOverlay.module.css'

interface PracticeToastProps {
  node: InteractionNode
  onContinue: () => void
}

export function PracticeToast({ node, onContinue }: PracticeToastProps) {
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
