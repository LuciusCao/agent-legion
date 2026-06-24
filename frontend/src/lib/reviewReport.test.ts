import { describe, expect, it } from 'vitest'
import { isReviewArtifact, parseReviewReport } from './reviewReport'

describe('isReviewArtifact', () => {
  it('returns true for known review reports', () => {
    expect(isReviewArtifact('key_info_review_report.json')).toBe(true)
    expect(isReviewArtifact('possible_errors_review_report.json')).toBe(true)
    expect(isReviewArtifact('review_result.json')).toBe(true)
  })

  it('returns false for unrelated artifacts', () => {
    expect(isReviewArtifact('questions.json')).toBe(false)
    expect(isReviewArtifact('comprehension_info.json')).toBe(false)
  })
})

describe('parseReviewReport', () => {
  it('parses key_info review report', () => {
    const content = JSON.stringify({
      question_id: 'q1',
      approved_count: 2,
      rejected_count: 1,
      warnings: ['warning'],
      decisions: [
        { key_info_id: 'ki_1', decision: 'approved', reason: 'good' },
        { key_info_id: 'ki_2', decision: 'rejected', reason: 'bad' },
      ],
    })
    const report = parseReviewReport('key_info_review_report.json', content)
    expect(report.name).toBe('key_info_review_report.json')
    expect(report.title).toBe('审核关键信息')
    expect(report.summary).toEqual({
      approved: 2,
      rejected: 1,
      warnings: ['warning'],
    })
    expect(report.decisions).toHaveLength(2)
    expect(report.decisions[0]).toEqual({
      id: 'ki_1',
      decision: 'approved',
      reason: 'good',
    })
  })

  it('parses possible_errors review report', () => {
    const content = JSON.stringify({
      question_id: 'q1',
      approved_count: 1,
      rejected_count: 0,
      warnings: [],
      decisions: [{ error_id: 'pe_1', decision: 'approved', reason: 'ok' }],
    })
    const report = parseReviewReport(
      'possible_errors_review_report.json',
      content
    )
    expect(report.title).toBe('审核可能审题错误')
    expect(report.decisions[0].id).toBe('pe_1')
  })

  it('parses content review_result', () => {
    const content = JSON.stringify({
      review_status: 'approved',
      review_msg: 'all good',
      details: [{ item: 'x', result: 'approved' }],
    })
    const report = parseReviewReport('review_result.json', content)
    expect(report.title).toBe('内容审核')
    expect(report.summary.approved).toBe(1)
    expect(report.decisions[0].decision).toBe('approved')
  })

  it('preserves needs_revision as a distinct decision', () => {
    const content = JSON.stringify({
      review_status: 'needs_revision',
      review_msg: 'please revise',
      details: [{ item: 'x', result: 'needs_revision', reason: 'unclear' }],
    })
    const report = parseReviewReport('review_result.json', content)
    expect(report.decisions[0].decision).toBe('needs_revision')
    expect(report.decisions[0].reason).toBe('unclear')
    expect(report.summary.rejected).toBe(0)
  })

  it('preserves needs_revision in key_info decisions', () => {
    const content = JSON.stringify({
      question_id: 'q1',
      approved_count: 0,
      rejected_count: 0,
      warnings: [],
      decisions: [
        { key_info_id: 'ki_1', decision: 'needs_revision', reason: 'revise' },
      ],
    })
    const report = parseReviewReport('key_info_review_report.json', content)
    expect(report.decisions[0].decision).toBe('needs_revision')
  })

  it('returns empty decisions but keeps raw for unknown review_result shapes', () => {
    const content = JSON.stringify({
      status: 'pending',
      message: 'not yet finalized',
      custom_field: 123,
    })
    const report = parseReviewReport('review_result.json', content)
    expect(report.decisions).toEqual([])
    expect(report.raw).toEqual({
      status: 'pending',
      message: 'not yet finalized',
      custom_field: 123,
    })
  })

  it('returns empty report for invalid json', () => {
    const report = parseReviewReport('key_info_review_report.json', 'not json')
    expect(report.decisions).toEqual([])
    expect(report.summary.approved).toBe(0)
  })
})
