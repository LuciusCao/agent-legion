import { useMemo, useRef, useState } from 'react'
import { useJobQuestion } from '../hooks/useJobQuestion'
import { useJobComprehensionInfo } from '../hooks/useJobComprehensionInfo'
import { useJobReviewReports } from '../hooks/useJobReviewReports'
import { buildHighlightedStemParts } from '../lib/questionHighlight'
import { escapeHtml } from '../lib/htmlText'
import { ErrorAnswerBadges } from './ErrorAnswerBadges'
import { QuestionAnnotations } from './QuestionAnnotations'
import { RichText } from './RichText'
import { MaterialIcon } from './MaterialIcon'
import { SocraticQuestion } from './SocraticQuestion'
import {
  ReviewChipStatus,
  ReviewDetailStatus,
  useReviewDecisionMaps,
} from './QuestionContentReview'
import { QuestionAnalysisSection } from './QuestionAnalysisSection'
import styles from './QuestionContentPanel.module.css'
import type { KeyInfoItem, PossibleErrorItem } from '../types'

function extractAnswerItems(answer: unknown): string[] | null {
  if (answer == null) return null
  if (typeof answer === 'string') return [answer]
  if (Array.isArray(answer) && answer.every((a) => typeof a === 'string')) {
    return answer
  }
  if (typeof answer === 'object') {
    const obj = answer as Record<string, unknown>
    if (typeof obj.value === 'string') return [obj.value]
    if (typeof obj.answer === 'string') return [obj.answer]
    if (
      Array.isArray(obj.value) &&
      obj.value.every((a) => typeof a === 'string')
    ) {
      return obj.value
    }
    if (
      Array.isArray(obj.answer) &&
      obj.answer.every((a) => typeof a === 'string')
    ) {
      return obj.answer
    }
  }
  return null
}

export interface QuestionContentPanelProps {
  jobId: string
  refreshKey?: string
  comprehensionRefreshKey?: string
  keyInfoPreviewable?: boolean
  possibleErrorsPreviewable?: boolean
  keyInfoReviewAttempted?: boolean
  possibleErrorsReviewAttempted?: boolean
}

export function QuestionContentPanel({
  jobId,
  refreshKey,
  comprehensionRefreshKey,
  keyInfoPreviewable = false,
  possibleErrorsPreviewable = false,
  keyInfoReviewAttempted = false,
  possibleErrorsReviewAttempted = false,
}: QuestionContentPanelProps) {
  const { question, loading, error } = useJobQuestion(jobId, refreshKey)
  const { info: comprehensionInfo } = useJobComprehensionInfo(
    jobId,
    comprehensionRefreshKey ?? refreshKey
  )
  const { reports: reviewReports } = useJobReviewReports(
    jobId,
    keyInfoReviewAttempted,
    possibleErrorsReviewAttempted,
    comprehensionRefreshKey ?? refreshKey
  )
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [selectedErrorId, setSelectedErrorId] = useState<string | null>(null)
  const stemWrapperRef = useRef<HTMLDivElement>(null)

  const keyInfoList = useMemo(
    () => comprehensionInfo?.comprehension_data?.key_info_list ?? [],
    [comprehensionInfo]
  )
  const possibleErrorList = useMemo(
    () => comprehensionInfo?.comprehension_data?.possible_error_list ?? [],
    [comprehensionInfo]
  )

  const { keyInfoDecisions, possibleErrorDecisions } =
    useReviewDecisionMaps(reviewReports)

  const stem = question?.stem
  const selectedKeyInfos = useMemo(() => {
    return Array.from(selectedIds)
      .map((id) => keyInfoList.find((k) => k.key_info_id === id))
      .filter((k): k is KeyInfoItem => Boolean(k))
  }, [selectedIds, keyInfoList])

  const selectedError = useMemo(
    () => possibleErrorList.find((e) => e.error_id === selectedErrorId) ?? null,
    [selectedErrorId, possibleErrorList]
  )

  const selectedErrorDecision = useMemo(
    () =>
      selectedError
        ? possibleErrorDecisions.get(selectedError.error_id)
        : undefined,
    [selectedError, possibleErrorDecisions]
  )

  const errorKeyInfos = useMemo(() => {
    if (!selectedError) return []
    return selectedError.related_key_info_ids
      .map((id) => keyInfoList.find((k) => k.key_info_id === id))
      .filter((k): k is KeyInfoItem => Boolean(k))
  }, [selectedError, keyInfoList])

  const highlightedKeyInfos = useMemo(
    () => [...selectedKeyInfos, ...errorKeyInfos],
    [selectedKeyInfos, errorKeyInfos]
  )

  const highlightedStemParts = useMemo(() => {
    if (!stem || highlightedKeyInfos.length === 0) return null
    return buildHighlightedStemParts(stem, highlightedKeyInfos)
  }, [stem, highlightedKeyInfos])

  const hiddenKeyInfos = useMemo(
    () => highlightedKeyInfos.filter((k) => k.type === 'hidden'),
    [highlightedKeyInfos]
  )

  const analysisSteps = question?.analysis_steps

  const rawAnswer = question?.answer
  const answerItems = useMemo(
    () => (rawAnswer ? extractAnswerItems(rawAnswer) : null),
    [rawAnswer]
  )
  const answer_blanks = question?.answer_blanks

  if (loading) {
    return <p className={styles.loading}>加载题目中...</p>
  }

  if (error) {
    return <p className={styles.error}>{error}</p>
  }

  if (!question) {
    return <p className={styles.empty}>题目数据尚未生成</p>
  }

  return (
    <div className={styles.panel}>
      <section className={styles.card}>
        <h2 className={styles.sectionTitle}>题干</h2>
        <div ref={stemWrapperRef} className={styles.stemWrapper}>
          <div className={styles.stemMain}>
            {question.stem ? (
              highlightedStemParts ? (
                <div className={styles.richText}>
                  {highlightedStemParts.map((part, idx) => (
                    <span
                      key={idx}
                      className={
                        part.type === 'highlight'
                          ? part.corrected
                            ? 'highlight-corrected'
                            : 'highlight'
                          : undefined
                      }
                      data-ids={part.ids?.join(',')}
                    >
                      {/* part.text is plain text extracted from HTML; escape it before
                          passing to block mode, which sanitizes and then renders LaTeX
                          inside text nodes. */}
                      <RichText mode="block">{escapeHtml(part.text)}</RichText>
                    </span>
                  ))}
                </div>
              ) : (
                <div className={styles.richText}>
                  <RichText mode="block">{stem ?? ''}</RichText>
                </div>
              )
            ) : (
              <p className={styles.empty}>无题干</p>
            )}
          </div>
          <QuestionAnnotations
            wrapperRef={stemWrapperRef}
            hiddenItems={hiddenKeyInfos}
          />
        </div>
      </section>

      {keyInfoPreviewable && keyInfoList.length > 0 && (
        <section className={styles.card}>
          <div className={styles.comprehensionChips}>
            <div className={styles.chipsHeader}>
              <h2 className={styles.sectionTitle} style={{ margin: 0 }}>
                审题信息
                <span className={styles.chipsCount}>
                  {keyInfoList.length} 个信息点
                </span>
              </h2>
            </div>
            <div className={styles.chipRow}>
              {keyInfoList.map((info, idx) => {
                const isSelected = selectedIds.has(info.key_info_id)
                const decision = keyInfoDecisions.get(info.key_info_id)
                const labelSource =
                  info.content.text ||
                  info.content.derived_text ||
                  `信息点 ${idx + 1}`
                const label =
                  labelSource.length > 12
                    ? labelSource.slice(0, 12) + '…'
                    : labelSource
                return (
                  <button
                    key={info.key_info_id}
                    className={`${styles.chip} ${
                      isSelected ? styles.chipSelected : ''
                    }`}
                    onClick={() => {
                      setSelectedIds((prev) => {
                        if (prev.has(info.key_info_id)) {
                          return new Set()
                        }
                        return new Set([info.key_info_id])
                      })
                      setSelectedErrorId(null)
                    }}
                  >
                    <span className={styles.chipIndex}>{idx + 1}</span>
                    <RichText mode="inline">{label}</RichText>
                    {decision && <ReviewChipStatus decision={decision} />}
                  </button>
                )
              })}
            </div>
          </div>

          {selectedKeyInfos.length > 0 && (
            <div className={styles.detailPanel}>
              {selectedKeyInfos.map((info) => {
                const typeLabel =
                  info.type === 'given' ? '题干信息' : '隐含信息'
                const errors = possibleErrorList.filter(
                  (e: PossibleErrorItem) =>
                    e.related_key_info_ids.includes(info.key_info_id)
                )
                const decision = keyInfoDecisions.get(info.key_info_id)
                return (
                  <div key={info.key_info_id} className={styles.detailCard}>
                    <div className={styles.detailCardHeader}>
                      <span
                        className={`${styles.typeBadge} ${
                          info.type === 'given'
                            ? styles.typeBadgeGiven
                            : styles.typeBadgeHidden
                        }`}
                      >
                        {typeLabel}
                      </span>
                      <span className={styles.detailId}>
                        {info.key_info_id}
                      </span>
                    </div>
                    <div className={styles.detailText}>
                      <RichText mode="inline">
                        {info.type === 'hidden'
                          ? info.content.derivation || ''
                          : info.content.text ||
                            info.content.derived_text ||
                            ''}
                      </RichText>
                    </div>
                    <div className={styles.detailSection}>
                      <strong>关联能力</strong>
                      <div className={styles.abilityList}>
                        <span className={styles.abilityTag}>
                          {info.question_comprehension_ability}
                        </span>
                      </div>
                    </div>
                    {errors.length > 0 && (
                      <div className={styles.detailSection}>
                        <strong>关联常见错误</strong>
                        <ul>
                          {errors.map((err) => (
                            <li key={err.error_id}>
                              <RichText mode="inline">
                                {err.error_description}
                              </RichText>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <SocraticQuestion question={info.question} />
                    {decision && <ReviewDetailStatus decision={decision} />}
                  </div>
                )
              })}
            </div>
          )}
        </section>
      )}

      {question.options && question.options.length > 0 && (
        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>选项</h2>
          <ul className={styles.optionList}>
            {question.options.map((opt, idx) => {
              const label = String(opt.label || String.fromCharCode(65 + idx))
              const content = String(opt.content || '')
              const isCorrect =
                answerItems != null && answerItems.includes(label)
              return (
                <li
                  key={idx}
                  className={`${styles.optionItem} ${
                    isCorrect ? styles.correct : ''
                  }`}
                >
                  {isCorrect && (
                    <MaterialIcon
                      name="check"
                      className={styles.checkIcon}
                      aria-hidden="true"
                    />
                  )}
                  <span className={styles.optionLabel}>{label}.</span>
                  <span className={styles.optionContent}>
                    <RichText mode="inline">{content}</RichText>
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {(answer_blanks != null || answerItems != null || rawAnswer != null) && (
        <section className={`${styles.card} ${styles.answerCard}`}>
          <h2 className={styles.sectionTitle}>答案</h2>
          {answer_blanks != null && answer_blanks.length > 0 ? (
            <div className={styles.answerBlankList}>
              {answer_blanks.map((blank, idx) => (
                <div key={idx} className={styles.answerBlank}>
                  <span className={styles.blankLabel}>第{idx + 1}空：</span>
                  <span className={styles.blankAlternatives}>
                    {blank.alternatives.map((alt, aidx) => (
                      <span key={aidx} className={styles.answerBadge}>
                        <RichText mode="inline">{alt}</RichText>
                      </span>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          ) : answerItems != null && answerItems.length > 0 ? (
            <div className={styles.answerBadges}>
              {answerItems.map((item, idx) => (
                <span key={idx} className={styles.answerBadge}>
                  <RichText mode="inline">{item}</RichText>
                </span>
              ))}
            </div>
          ) : (
            <p className={styles.empty}>无答案</p>
          )}
        </section>
      )}

      {possibleErrorsPreviewable && possibleErrorList.length > 0 && (
        <section className={styles.card}>
          <div className={styles.comprehensionChips}>
            <div className={styles.chipsHeader}>
              <h2 className={styles.sectionTitle} style={{ margin: 0 }}>
                常见审题错误
                <span className={styles.chipsCount}>
                  {possibleErrorList.length} 个易错点
                </span>
              </h2>
            </div>
            <div className={styles.chipRow}>
              {possibleErrorList.map((err, idx) => {
                const isSelected = selectedErrorId === err.error_id
                const decision = possibleErrorDecisions.get(err.error_id)
                const labelSource = err.error_description || `易错点 ${idx + 1}`
                const label =
                  (err.position != null ? `第${err.position}空：` : '') +
                  (labelSource.length > 12
                    ? labelSource.slice(0, 12) + '…'
                    : labelSource)
                return (
                  <button
                    key={err.error_id}
                    className={`${styles.chip} ${
                      isSelected ? styles.chipSelected : ''
                    }`}
                    onClick={() => {
                      setSelectedErrorId((prev) =>
                        prev === err.error_id ? null : err.error_id
                      )
                      setSelectedIds(new Set())
                    }}
                  >
                    <span className={styles.chipIndex}>{idx + 1}</span>
                    <RichText mode="inline">{label}</RichText>
                    {decision && <ReviewChipStatus decision={decision} />}
                  </button>
                )
              })}
            </div>
          </div>

          {selectedError && (
            <div className={styles.detailPanel}>
              <div className={styles.detailCard}>
                <div className={styles.detailCardHeader}>
                  <span className={styles.errorAnswerBadge}>
                    错误答案：
                    <ErrorAnswerBadges
                      answers={
                        Array.isArray(selectedError.error_answer)
                          ? selectedError.error_answer
                          : [selectedError.error_answer]
                      }
                    />
                  </span>
                  <span className={styles.detailId}>
                    {selectedError.error_id}
                  </span>
                </div>
                <div className={styles.detailText}>
                  <RichText mode="inline">
                    {selectedError.error_description}
                  </RichText>
                </div>
                <div className={styles.detailSection}>
                  <strong>关联关键信息</strong>
                  <div className={styles.relatedKeyInfoList}>
                    {errorKeyInfos.length > 0 ? (
                      errorKeyInfos.map((k) => (
                        <span
                          key={k.key_info_id}
                          className={styles.relatedKeyInfoTag}
                        >
                          <RichText mode="inline">
                            {k.content.text ||
                              k.content.derived_text ||
                              k.key_info_id}
                          </RichText>
                        </span>
                      ))
                    ) : (
                      <span className={styles.relatedKeyInfoTag}>无</span>
                    )}
                  </div>
                </div>
                {selectedErrorDecision ? (
                  <ReviewDetailStatus decision={selectedErrorDecision} />
                ) : null}
              </div>
            </div>
          )}
        </section>
      )}

      {(question.analysis != null ||
        (analysisSteps != null && analysisSteps.length > 0)) && (
        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>解析</h2>
          <QuestionAnalysisSection
            analysis={question.analysis}
            analysisSteps={analysisSteps}
          />
        </section>
      )}
    </div>
  )
}
