import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchQuestionDetail } from '../api'
import { JOB_STATUS_LABELS } from '../labels'
import { useUiStore } from '../stores/uiStore'
import type { JobRecord, QuestionDetailResponse } from '../types'
import styles from './QuestionDetailPage.module.css'

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

function progressText(job: JobRecord): string {
  const completed = job.completed_nodes ?? 0
  const total = job.total_nodes ?? 0
  if (total <= 0) return '—'
  return `${completed}/${total}`
}

export default function QuestionDetailPage() {
  const { workspaceId, questionId } = useParams<{
    workspaceId: string
    questionId: string
  }>()
  const navigate = useNavigate()
  const { setPageTitle } = useUiStore()
  const [detail, setDetail] = useState<QuestionDetailResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!workspaceId || !questionId) return
    let cancelled = false
    fetchQuestionDetail(workspaceId, questionId)
      .then((data) => {
        if (!cancelled) {
          setDetail(data)
          setPageTitle(`${data.title || '知识点'} - ${questionId}`)
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
      setPageTitle(null)
    }
  }, [workspaceId, questionId, setPageTitle])

  useEffect(() => {
    if (!workspaceId || !questionId) return
    let cancelled = false
    const timer = setInterval(() => {
      fetchQuestionDetail(workspaceId, questionId)
        .then((data) => {
          if (!cancelled) {
            setDetail(data)
            setPageTitle(`${data.title || '知识点'} - ${questionId}`)
          }
        })
        .catch(() => {})
    }, 5000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [workspaceId, questionId, setPageTitle])

  const stem = detail?.normalized.stem
  const stemHtml = useMemo(() => {
    if (!stem) return ''
    return sanitizeHtml(stem)
  }, [stem])

  const analysis = detail?.normalized.analysis
  const analysisHtml = useMemo(() => {
    if (!analysis) return ''
    const raw =
      typeof analysis === 'string' ? analysis : JSON.stringify(analysis)
    return sanitizeHtml(raw)
  }, [analysis])

  const handleRefresh = () => {
    if (!workspaceId || !questionId) return
    setLoading(true)
    setError('')
    fetchQuestionDetail(workspaceId, questionId)
      .then((data) => setDetail(data))
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err))
      )
      .finally(() => setLoading(false))
  }

  if (!workspaceId || !questionId) {
    return <p className={styles.error}>缺少参数</p>
  }

  if (loading && !detail) {
    return <p className={styles.loading}>加载中...</p>
  }

  return (
    <div className={styles.page}>
      {error ? <p className={styles.error}>{error}</p> : null}

      <div className={styles.columns}>
        <div className={styles.left}>
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

          {detail?.normalized.options &&
            detail.normalized.options.length > 0 && (
              <section className={styles.card}>
                <h2 className={styles.sectionTitle}>选项</h2>
                <ul className={styles.optionList}>
                  {detail.normalized.options.map((opt, idx) => {
                    const label = String(
                      (opt as Record<string, unknown>).label ||
                        String.fromCharCode(65 + idx)
                    )
                    const content = String(
                      (opt as Record<string, unknown>).content || ''
                    )
                    const isCorrect = Array.isArray(detail.normalized.answer)
                      ? detail.normalized.answer.includes(label)
                      : false
                    return (
                      <li
                        key={idx}
                        className={`${styles.optionItem} ${
                          isCorrect ? styles.correct : ''
                        }`}
                      >
                        <span className={styles.optionLabel}>{label}.</span>
                        <span className={styles.optionContent}>{content}</span>
                      </li>
                    )
                  })}
                </ul>
              </section>
            )}

          {detail?.normalized.answer != null && (
            <section className={styles.card}>
              <h2 className={styles.sectionTitle}>答案</h2>
              <pre className={styles.pre}>
                {JSON.stringify(detail.normalized.answer, null, 2)}
              </pre>
            </section>
          )}

          {detail?.normalized.analysis != null && (
            <section className={styles.card}>
              <h2 className={styles.sectionTitle}>解析</h2>
              <div
                className={styles.richText}
                dangerouslySetInnerHTML={{ __html: analysisHtml }}
              />
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

        <div className={styles.right}>
          <section className={styles.card}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>处理记录</h2>
              <md-icon-button
                aria-label="刷新"
                onClick={handleRefresh}
                title="刷新"
              >
                <md-icon>refresh</md-icon>
              </md-icon-button>
            </div>
            {detail && detail.jobs.length > 0 ? (
              <ul className={styles.jobList}>
                {detail.jobs.map((job) => (
                  <li key={job.id} className={styles.jobItem}>
                    <button
                      type="button"
                      className={styles.jobBtn}
                      onClick={() =>
                        navigate(`/workspaces/${workspaceId}/jobs/${job.id}`)
                      }
                    >
                      <div className={styles.jobTitle}>
                        {job.title || job.source_id}
                      </div>
                      <div className={styles.jobMeta}>
                        <span
                          className={`${styles.badge} ${
                            styles[job.status] || styles.pending
                          }`}
                        >
                          {JOB_STATUS_LABELS[job.status] || job.status}
                        </span>
                        <span className={styles.progress}>
                          {progressText(job)}
                        </span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className={styles.emptyState}>
                <p>暂无处理记录</p>
                <md-outlined-button
                  onClick={() => navigate(`/workspaces/${workspaceId}/jobs`)}
                >
                  创建任务
                </md-outlined-button>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
