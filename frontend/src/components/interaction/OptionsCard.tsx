import type { InteractionNode } from '../../types'
import { RichText } from '../RichText'
import styles from '../InteractionOverlay.module.css'
import type { InteractionOption } from './useSummaryOrder'

interface OptionsCardProps {
  node: InteractionNode
  options: InteractionOption[]
  onContinue: () => void
}

export function OptionsCard({ node, options, onContinue }: OptionsCardProps) {
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
