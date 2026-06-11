import { useEffect, useMemo, useState } from 'react'
import { fetchQuestionDetail } from '../api'
import type { QuestionDetailResponse } from '../types'
import { renderLatexInHtml } from '../lib/latex'
import { LaTeXText } from './LaTeXText'
import styles from './QuestionContentPanel.module.css'

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

function hasLatex(text: string): boolean {
  return /(\$\$[\s\S]*?\$\$)|(\$[^$\r\n]*?\$)|(\\\[[\s\S]*?\\\])|(\\\([\s\S]*?\\\))/.test(
    text
  )
}

export interface QuestionContentPanelProps {
  workspaceId: string
  questionId: string
}

export function QuestionContentPanel({
  workspaceId,
  questionId,
}: QuestionContentPanelProps) {
  const [detail, setDetail] = useState<QuestionDetailResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetchQuestionDetail(workspaceId, questionId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data)
        }
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [workspaceId, questionId])

  const stem = detail?.normalized.stem
  const stemHtml = useMemo(() => {
    if (!stem) return ''
    return renderLatexInHtml(sanitizeHtml(stem))
  }, [stem])

  const analysis = detail?.normalized.analysis
  const analysisHtml = useMemo(() => {
    if (!analysis || typeof analysis !== 'string') return ''
    return renderLatexInHtml(sanitizeHtml(analysis))
  }, [analysis])

  const rawAnswer = detail?.normalized.answer
  const answerItems = rawAnswer ? extractAnswerItems(rawAnswer) : null
  const answerBlanks = (
    detail?.normalized as {
      answerBlanks?: Array<{ alternatives: string[]; isLatex: boolean }>
    }
  )?.answerBlanks

  if (loading) {
    return <p className={styles.loading}>加载题目中...</p>
  }

  if (error) {
    return <p className={styles.error}>{error}</p>
  }

  return (
    <div className={styles.panel}>
      <section className={styles.card}>
        <h2 className={styles.sectionTitle}>题干</h2>
        {detail?.normalized.stem ? (
          <div
            className={styles.richText}
            dangerouslySetInnerHTML={{ __html: stemHtml }}
          />
        ) : (
          <p className={styles.empty}>未在 CMS 找到该题</p>
        )}
      </section>

      {detail?.normalized.options && detail.normalized.options.length > 0 && (
        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>选项</h2>
          <ul className={styles.optionList}>
            {detail.normalized.options.map((opt, idx) => {
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

      {(answerBlanks != null || answerItems != null || rawAnswer != null) && (
        <section className={`${styles.card} ${styles.answerCard}`}>
          <h2 className={styles.sectionTitle}>答案</h2>
          {answerBlanks != null && answerBlanks.length > 0 ? (
            <div className={styles.answerBlankList}>
              {answerBlanks.map((blank, idx) => (
                <div key={idx} className={styles.answerBlank}>
                  <span className={styles.blankLabel}>第{idx + 1}空：</span>
                  <span className={styles.blankAlternatives}>
                    {blank.alternatives.map((alt, aidx) => (
                      <span key={aidx} className={styles.answerBadge}>
                        {blank.isLatex && hasLatex(alt) ? (
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
                  {hasLatex(item) ? <LaTeXText>{item}</LaTeXText> : item}
                </span>
              ))}
            </div>
          ) : (
            <p className={styles.empty}>无答案</p>
          )}
        </section>
      )}

      {detail?.normalized.analysis != null && (
        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>解析</h2>
          {typeof detail.normalized.analysis === 'string' ? (
            <div
              className={styles.richText}
              dangerouslySetInnerHTML={{ __html: analysisHtml }}
            />
          ) : (
            <pre className={styles.pre}>
              {JSON.stringify(detail.normalized.analysis, null, 2)}
            </pre>
          )}
        </section>
      )}

      {detail?.cms_payload && (
        <details className={styles.card}>
          <summary>原始 CMS 数据</summary>
          <pre className={styles.pre}>
            {JSON.stringify(detail.cms_payload, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}
