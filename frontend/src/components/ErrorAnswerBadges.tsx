import { extractLatexParts } from '../lib/latex'
import { LaTeXText } from './LaTeXText'
import styles from './QuestionContentPanel.module.css'

export interface ErrorAnswerBadgesProps {
  answers: string[]
}

export function ErrorAnswerBadges({ answers }: ErrorAnswerBadgesProps) {
  return (
    <>
      {answers.map((answer, idx) => (
        <span key={idx} className={styles.answerBadge}>
          {extractLatexParts(answer).some((p) => p.type === 'latex') ? (
            <LaTeXText>{answer}</LaTeXText>
          ) : (
            answer
          )}
        </span>
      ))}
    </>
  )
}
