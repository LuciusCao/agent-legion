import { useEffect, useState } from 'react'

import { fetchJobs } from '../api'
import type { JobRecord } from '../types'

function getErrorStatus(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'status' in error) {
    return Number((error as { status?: unknown }).status)
  }
  return undefined
}

export function JobsPage() {
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [disabled, setDisabled] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    fetchJobs()
      .then((result) => {
        if (!cancelled) {
          setJobs(result.jobs)
        }
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return
        }
        if (getErrorStatus(err) === 404) {
          setDisabled(true)
        } else {
          setError('加载题目任务失败')
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  if (disabled) {
    return <main className="view">题目工厂未启用</main>
  }

  return (
    <main className="view">
      <header className="topbar">
        <div>
          <p className="phase-name">Agent Legion</p>
          <h1>题目工厂</h1>
        </div>
      </header>

      {error ? <p role="alert">{error}</p> : null}

      <section className="video-list">
        {jobs.length === 0 ? (
          <p>暂无题目任务</p>
        ) : (
          jobs.map((job) => (
            <article className="card-outlined resource-row" key={job.id}>
              <div className="resource-main">
                <strong>{job.title}</strong>
                <small>{job.source_id}</small>
              </div>
              <span className="status-badge">{job.status}</span>
            </article>
          ))
        )}
      </section>
    </main>
  )
}
