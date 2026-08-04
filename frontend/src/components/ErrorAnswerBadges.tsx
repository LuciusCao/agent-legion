import { RichText } from './RichText'
import styles from './question/QuestionContentPanel.module.css'

export interface ErrorAnswerBadgesProps {
  answers: string[]
}

export function ErrorAnswerBadges({ answers }: ErrorAnswerBadgesProps) {
  return (
    <>
      {answers.map((answer, idx) => (
        <span key={idx} className={styles.answerBadge}>
          <RichText mode="inline">{answer}</RichText>
        </span>
      ))}
    </>
  )
}
