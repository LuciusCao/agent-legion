import { RichText } from './RichText'
import styles from './SocraticQuestion.module.css'
import type { KeyInfoItem } from '../types'

export interface SocraticQuestionProps {
  question?: KeyInfoItem['question']
}

export function SocraticQuestion({ question }: SocraticQuestionProps) {
  if (!question) return null
  const hasText = Boolean(question.text)
  const hasOptions = question.options.length > 0
  if (!hasText && !hasOptions) return null

  return (
    <div className={styles.socraticSection}>
      <div className={styles.socraticTitle}>苏格拉底提问</div>
      {hasText && (
        <div className={styles.socraticText}>
          <RichText mode="inline">{question.text}</RichText>
        </div>
      )}
      {hasOptions && (
        <ul className={styles.socraticOptionList}>
          {question.options.map((opt, idx) => {
            const label = String(opt.label || String.fromCharCode(65 + idx))
            const text = String(opt.text || '')
            return (
              <li
                key={idx}
                className={`${styles.socraticOptionItem} ${
                  opt.is_correct ? styles.socraticOptionCorrect : ''
                }`}
              >
                <span className={styles.socraticOptionLabel}>{label}.</span>
                <span className={styles.socraticOptionContent}>
                  <RichText mode="inline">{text}</RichText>
                </span>
                {opt.is_correct && (
                  <span className={styles.socraticCorrectMark}>✓ 正确</span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
