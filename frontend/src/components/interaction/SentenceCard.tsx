import { Button } from '@mui/material'
import type { InteractionNode } from '../../types'
import { RichText } from '../RichText'
import styles from '../InteractionOverlay.module.css'

interface SentenceCardProps {
  node: InteractionNode
  currentSentence: string[]
  onWordClick: (word: string) => void
  onReset: () => void
  onContinue: () => void
}

export function SentenceCard({
  node,
  currentSentence,
  onWordClick,
  onReset,
  onContinue,
}: SentenceCardProps) {
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
