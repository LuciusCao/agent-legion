import type { InteractionNode } from '../types'
import { OptionsCard } from './interaction/OptionsCard'
import { PracticeToast } from './interaction/PracticeToast'
import { SentenceCard } from './interaction/SentenceCard'
import { SummaryInteractionCard } from './interaction/SummaryInteractionCard'

interface InteractionOverlayProps {
  node: InteractionNode | null
  currentSentence: string[]
  onWordClick: (word: string) => void
  onReset: () => void
  onContinue: () => void
}

export function InteractionOverlay({
  node,
  currentSentence,
  onWordClick,
  onReset,
  onContinue,
}: InteractionOverlayProps) {
  if (!node) return null

  const type = String(node.type ?? '')
  const options = node.options ?? []
  const isSummary = type === 'interaction_summary' || type === 'video_summary'

  if (type === 'example_practice') {
    return <PracticeToast node={node} onContinue={onContinue} />
  }

  if (isSummary && options.length > 0) {
    return (
      <SummaryInteractionCard
        node={node}
        options={options}
        onContinue={onContinue}
      />
    )
  }

  if (options.length > 0) {
    return <OptionsCard node={node} options={options} onContinue={onContinue} />
  }

  // Fallback: sentence-building or generic interaction
  return (
    <SentenceCard
      node={node}
      currentSentence={currentSentence}
      onWordClick={onWordClick}
      onReset={onReset}
      onContinue={onContinue}
    />
  )
}
