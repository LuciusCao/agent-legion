import { RichText } from './RichText'
import panelStyles from './QuestionContentPanel.module.css'
import styles from './QuestionAnalysisSection.module.css'
import type { AnalysisStep } from '../types'

export function QuestionAnalysisSection({
  analysis,
  analysisSteps,
}: {
  analysis: unknown
  analysisSteps?: AnalysisStep[][] | null
}) {
  if (analysisSteps != null && analysisSteps.length > 0) {
    return (
      <div className={styles.analysisGroups}>
        {analysisSteps.map((group, gidx) => (
          <div key={gidx} className={styles.analysisGroup}>
            {group.map((step, sidx) => (
              <div key={sidx} className={styles.analysisStep}>
                {step.title ? (
                  <h4 className={styles.stepTitle}>
                    <RichText mode="block">{step.title}</RichText>
                  </h4>
                ) : null}
                <div className={panelStyles.richText}>
                  <RichText mode="block">{step.content}</RichText>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    )
  }

  if (typeof analysis === 'string') {
    return (
      <div className={panelStyles.richText}>
        <RichText mode="block">{analysis}</RichText>
      </div>
    )
  }

  return <pre className={styles.pre}>{JSON.stringify(analysis, null, 2)}</pre>
}
