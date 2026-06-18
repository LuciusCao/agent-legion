import React from 'react'
import { KNOWLEDGE_PHASES, PHASE_LABELS, QUESTION_PHASES } from '../labels'
import type { VideoItem } from '../types'
import styles from './PhaseStepper.module.css'

type StepState = 'completed' | 'running' | 'failed' | 'pending'

function getStepState(
  video: VideoItem,
  phaseIndex: number,
  currentIndex: number
): StepState {
  if (video.status === 'completed') return 'completed'
  if (video.current_phase === 'waiting_for_url') return 'pending'

  if (phaseIndex < currentIndex) return 'completed'
  if (phaseIndex > currentIndex) return 'pending'

  // phaseIndex === currentIndex
  if (video.status === 'failed') return 'failed'
  if (video.status === 'running') return 'running'
  return 'pending'
}

export const PhaseStepper = React.memo(function PhaseStepper({
  video,
}: {
  video: VideoItem
}) {
  const phases =
    video.content_type === 'question' ? QUESTION_PHASES : KNOWLEDGE_PHASES
  const currentIndex = phases.indexOf(video.current_phase)

  return (
    <div className={styles.phaseStepper}>
      {phases.map((phase, index) => {
        const state = getStepState(video, index, currentIndex)
        return (
          <div key={phase} className={styles.step} title={PHASE_LABELS[phase]}>
            <div
              /* .pulse-blue is a global utility class defined in styles.css */
              className={`${styles.stepBar} ${styles[state]} ${state === 'running' ? 'pulse-blue' : ''}`}
            />
          </div>
        )
      })}
    </div>
  )
})
