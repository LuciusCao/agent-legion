import { describe, it, expect } from 'vitest'
import { classifyArtifactPreview, PREVIEW_KIND_LABELS } from './previewKind'

describe('classifyArtifactPreview', () => {
  it('按扩展名映射渲染原语', () => {
    expect(classifyArtifactPreview('questions.json')).toBe('json')
    expect(classifyArtifactPreview('README.md')).toBe('markdown')
    expect(classifyArtifactPreview('notes.markdown')).toBe('markdown')
    expect(classifyArtifactPreview('report.html')).toBe('richtext')
    expect(classifyArtifactPreview('report.htm')).toBe('richtext')
    expect(classifyArtifactPreview('frame.png')).toBe('image')
    expect(classifyArtifactPreview('frame.JPG')).toBe('image')
    expect(classifyArtifactPreview('clip.mp4')).toBe('video')
    expect(classifyArtifactPreview('voice.mp3')).toBe('audio')
    expect(classifyArtifactPreview('doc.pdf')).toBe('pdf')
    expect(classifyArtifactPreview('run.log')).toBe('text')
    expect(classifyArtifactPreview('data.csv')).toBe('text')
  })

  it('svg 强制按 text 渲染源码（防内联脚本）', () => {
    expect(classifyArtifactPreview('diagram.svg')).toBe('text')
  })

  it('未知扩展名与无扩展名兜底 text', () => {
    expect(classifyArtifactPreview('archive.tar.gz')).toBe('text')
    expect(classifyArtifactPreview('Makefile')).toBe('text')
    expect(classifyArtifactPreview('png')).toBe('text')
    expect(classifyArtifactPreview('trailing.')).toBe('text')
    expect(classifyArtifactPreview('.hidden')).toBe('text')
  })

  it('标签表覆盖全部 kind', () => {
    for (const label of Object.values(PREVIEW_KIND_LABELS)) {
      expect(label.length).toBeGreaterThan(0)
    }
  })
})
