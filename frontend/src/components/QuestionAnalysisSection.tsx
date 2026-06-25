import { renderLatexInHtml } from '../lib/latex'
import { sanitizeHtml } from '../lib/sanitizeHtml'
import panelStyles from './QuestionContentPanel.module.css'
import styles from './QuestionAnalysisSection.module.css'
import type { AnalysisStep } from '../types'

export function QuestionAnalysisSection({
  analysis,
  analysisSteps,
}: {
  analysis: unknown
  analysisSteps?: AnalysisStep[][]
}) {
  const analysisHtml =
    typeof analysis === 'string'
      ? renderLatexInHtml(sanitizeHtml(analysis))
      : ''

  if (analysisSteps != null && analysisSteps.length > 0) {
    return (
      <div className={styles.analysisGroups}>
        {analysisSteps.map((group, gidx) => (
          <div key={gidx} className={styles.analysisGroup}>
            {group.map((step, sidx) => (
              <div key={sidx} className={styles.analysisStep}>
                {step.title ? (
                  <h4
                    className={styles.stepTitle}
                    dangerouslySetInnerHTML={{
                      __html: renderLatexInHtml(sanitizeHtml(step.title)),
                    }}
                  />
                ) : null}
                <div
                  className={panelStyles.richText}
                  dangerouslySetInnerHTML={{
                    __html: renderLatexInHtml(sanitizeHtml(step.content)),
                  }}
                />
              </div>
            ))}
          </div>
        ))}
      </div>
    )
  }

  if (typeof analysis === 'string') {
    return (
      <div
        className={panelStyles.richText}
        dangerouslySetInnerHTML={{ __html: analysisHtml }}
      />
    )
  }

  return <pre className={styles.pre}>{JSON.stringify(analysis, null, 2)}</pre>
}
