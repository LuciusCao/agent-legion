import { useMemo, useRef, useState } from 'react'
import { useJobQuestion } from '../hooks/useJobQuestion'
import { useJobComprehensionInfo } from '../hooks/useJobComprehensionInfo'
import { extractLatexParts, renderLatexInHtml } from '../lib/latex'
import { buildHighlightedStemHtml } from '../lib/questionHighlight'
import { QuestionAnnotations } from './QuestionAnnotations'
import { LaTeXText } from './LaTeXText'
import styles from './QuestionContentPanel.module.css'
import type { KeyInfoItem, PossibleErrorItem } from '../types'

const ALLOWED_TAGS = new Set([
  'P',
  'BR',
  'STRONG',
  'EM',
  'UL',
  'OL',
  'LI',
  'SPAN',
  'DIV',
])

function sanitizeHtml(html: string): string {
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT)
  const toRemove: Element[] = []
  while (walker.nextNode()) {
    const el = walker.currentNode as Element
    if (!ALLOWED_TAGS.has(el.tagName)) {
      toRemove.push(el)
    }
  }
  toRemove.forEach((el) => {
    const parent = el.parentNode
    if (!parent) return
    while (el.firstChild) {
      parent.insertBefore(el.firstChild, el)
    }
    parent.removeChild(el)
  })
  const attrWalker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT)
  while (attrWalker.nextNode()) {
    const el = attrWalker.currentNode as Element
    if (ALLOWED_TAGS.has(el.tagName)) {
      while (el.attributes.length > 0) {
        el.removeAttribute(el.attributes[0].name)
      }
    }
  }
  return doc.body.innerHTML
}

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
}

export function QuestionContentPanel({
  jobId,
  refreshKey,
}: QuestionContentPanelProps) {
  const { question, loading, error } = useJobQuestion(jobId, refreshKey)
  const { info: comprehensionInfo } = useJobComprehensionInfo(jobId, refreshKey)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const stemWrapperRef = useRef<HTMLDivElement>(null)

  const keyInfoList = useMemo(
    () => comprehensionInfo?.comprehension_data?.key_info_list ?? [],
    [comprehensionInfo]
  )
  const possibleErrorList = useMemo(
    () => comprehensionInfo?.comprehension_data?.possible_error_list ?? [],
    [comprehensionInfo]
  )

  const stem = question?.stem
  const stemHtml = useMemo(() => {
    if (!stem) return ''
    return renderLatexInHtml(sanitizeHtml(stem))
  }, [stem])

  const selectedKeyInfos = useMemo(() => {
    return Array.from(selectedIds)
      .map((id) => keyInfoList.find((k) => k.key_info_id === id))
      .filter((k): k is KeyInfoItem => Boolean(k))
  }, [selectedIds, keyInfoList])

  const highlightedStemHtml = useMemo(() => {
    if (!stem || selectedKeyInfos.length === 0) return stemHtml
    return renderLatexInHtml(buildHighlightedStemHtml(stem, selectedKeyInfos))
  }, [stem, stemHtml, selectedKeyInfos])

  const hiddenKeyInfos = useMemo(
    () => selectedKeyInfos.filter((k) => k.type === 'hidden'),
    [selectedKeyInfos]
  )

  const analysis = question?.analysis
  const analysisHtml = useMemo(() => {
    if (!analysis || typeof analysis !== 'string') return ''
    return renderLatexInHtml(sanitizeHtml(analysis))
  }, [analysis])

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
              <div
                className={styles.richText}
                dangerouslySetInnerHTML={{
                  __html:
                    selectedKeyInfos.length > 0
                      ? highlightedStemHtml
                      : stemHtml,
                }}
              />
            ) : (
              <p className={styles.empty}>无题干</p>
            )}
          </div>
          <QuestionAnnotations
            wrapperRef={stemWrapperRef}
            hiddenItems={hiddenKeyInfos}
          />
        </div>

        {keyInfoList.length > 0 && (
          <div className={styles.comprehensionChips}>
            <div className={styles.chipsHeader}>
              <h3 className={styles.sectionTitle} style={{ margin: 0 }}>
                审题信息
              </h3>
              <span className={styles.chipsCount}>
                {keyInfoList.length} 个信息点
              </span>
            </div>
            <div className={styles.chipRow}>
              {keyInfoList.map((info, idx) => {
                const isSelected = selectedIds.has(info.key_info_id)
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
                        const next = new Set(prev)
                        if (next.has(info.key_info_id)) {
                          next.delete(info.key_info_id)
                        } else {
                          next.add(info.key_info_id)
                        }
                        return next
                      })
                    }}
                  >
                    <span className={styles.chipIndex}>{idx + 1}</span>
                    <LaTeXText>{label}</LaTeXText>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {selectedKeyInfos.length > 0 && (
          <div className={styles.detailPanel}>
            {selectedKeyInfos.map((info) => {
              const typeLabel = info.type === 'given' ? '题干信息' : '隐含信息'
              const errors = possibleErrorList.filter((e: PossibleErrorItem) =>
                e.related_key_info_ids.includes(info.key_info_id)
              )
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
                    <span className={styles.detailId}>{info.key_info_id}</span>
                  </div>
                  <div className={styles.detailText}>
                    <LaTeXText>
                      {info.content.text || info.content.derived_text || ''}
                    </LaTeXText>
                  </div>
                  {info.type === 'hidden' && (
                    <div
                      className={styles.detailSection}
                      style={{ color: 'var(--md-sys-color-tertiary)' }}
                    >
                      👉 推导过程见题干右侧批注
                    </div>
                  )}
                  <div className={styles.detailSection}>
                    <strong>关联能力</strong>
                    <div className={styles.abilityList}>
                      {info.question_comprehension_abilities.map((ability) => (
                        <span key={ability} className={styles.abilityTag}>
                          {ability}
                        </span>
                      ))}
                    </div>
                  </div>
                  {errors.length > 0 && (
                    <div className={styles.detailSection}>
                      <strong>常见审题错误</strong>
                      <ul>
                        {errors.map((err) => (
                          <li key={err.error_id}>
                            <LaTeXText>{err.error_description}</LaTeXText>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

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
                    <md-icon className={styles.checkIcon} aria-hidden="true">
                      check
                    </md-icon>
                  )}
                  <span className={styles.optionLabel}>{label}.</span>
                  <span className={styles.optionContent}>
                    <LaTeXText>{content}</LaTeXText>
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
                        {blank.is_latex &&
                        extractLatexParts(alt).some(
                          (p) => p.type === 'latex'
                        ) ? (
                          <LaTeXText>{alt}</LaTeXText>
                        ) : (
                          alt
                        )}
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
                  {extractLatexParts(item).some((p) => p.type === 'latex') ? (
                    <LaTeXText>{item}</LaTeXText>
                  ) : (
                    item
                  )}
                </span>
              ))}
            </div>
          ) : (
            <p className={styles.empty}>无答案</p>
          )}
        </section>
      )}

      {(question.analysis != null ||
        (analysisSteps != null && analysisSteps.length > 0)) && (
        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>解析</h2>
          {analysisSteps != null && analysisSteps.length > 0 ? (
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
                        className={styles.richText}
                        dangerouslySetInnerHTML={{
                          __html: renderLatexInHtml(sanitizeHtml(step.content)),
                        }}
                      />
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : typeof question.analysis === 'string' ? (
            <div
              className={styles.richText}
              dangerouslySetInnerHTML={{ __html: analysisHtml }}
            />
          ) : (
            <pre className={styles.pre}>
              {JSON.stringify(question.analysis, null, 2)}
            </pre>
          )}
        </section>
      )}
    </div>
  )
}
