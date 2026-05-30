import { KNOWLEDGE_PHASES, QUESTION_PHASES } from '../labels'
import type { ContentType, VideoItem } from '../types'

export function computeProgress(video: VideoItem): number {
  if (video.status === 'completed') return 1
  if (video.current_phase === 'waiting_for_url') return 0

  const phases =
    video.content_type === 'question' ? QUESTION_PHASES : KNOWLEDGE_PHASES
  const index = phases.indexOf(video.current_phase)
  if (index === -1) return 0

  if (video.status === 'running') {
    return (index + 0.5) / phases.length
  }
  return index / phases.length
}

export function getPhases(contentType: ContentType): string[] {
  return contentType === 'question' ? QUESTION_PHASES : KNOWLEDGE_PHASES
}

export function getSharedPhases(contentTypes: ContentType[]): string[] {
  const unique = Array.from(new Set(contentTypes))
  if (unique.length === 0) return KNOWLEDGE_PHASES
  return unique.reduce<string[]>((shared, type) => {
    const phases = getPhases(type)
    return shared.filter((phase) => phases.includes(phase))
  }, getPhases(unique[0]))
}

export function canRerunFrom(video: VideoItem, phase: string): boolean {
  if (video.status === 'running') return false
  if (video.status === 'completed') return true
  const phases = getPhases(video.content_type)
  const currentIdx = phases.indexOf(video.current_phase)
  const phaseIdx = phases.indexOf(phase)
  if (currentIdx === -1 || phaseIdx === -1) return false
  return phaseIdx <= currentIdx
}

export function canContinueTo(video: VideoItem, targetPhase: string): boolean {
  if (video.status === 'running') return false
  if (video.status === 'failed' || video.status === 'completed') return false
  const phases = getPhases(video.content_type)
  const targetIdx = phases.indexOf(targetPhase)
  if (targetIdx === -1) return false
  if (video.current_phase === 'waiting_for_url') return true
  const currentIdx = phases.indexOf(video.current_phase)
  if (currentIdx === -1) return false
  return targetIdx >= currentIdx
}

export function canRerunTo(
  video: VideoItem,
  startPhase: string,
  targetPhase: string
): boolean {
  if (video.status === 'running') return false
  if (!canRerunFrom(video, startPhase)) return false
  const phases = getPhases(video.content_type)
  const startIdx = phases.indexOf(startPhase)
  const targetIdx = phases.indexOf(targetPhase)
  if (startIdx === -1 || targetIdx === -1) return false
  return startIdx <= targetIdx
}
