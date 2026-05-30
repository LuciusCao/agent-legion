import { create } from 'zustand'

interface InteractionState {
  triggeredNodeIndexes: Set<number>
  dismissedNodeIndexes: Set<number>
  currentSentence: string[]
  triggerInteraction: (index: number) => void
  dismissInteraction: (index: number) => void
  replayInteraction: (index: number) => void
  resetSentence: () => void
  pushWord: (word: string) => void
  clearSentence: () => void
  clearInteractions: () => void
}

export const useInteractionStore = create<InteractionState>((set) => ({
  triggeredNodeIndexes: new Set(),
  dismissedNodeIndexes: new Set(),
  currentSentence: [],
  triggerInteraction: (index) => {
    set((state) => {
      const next = new Set(state.triggeredNodeIndexes)
      next.add(index)
      return { triggeredNodeIndexes: next }
    })
  },
  dismissInteraction: (index) => {
    set((state) => {
      const nextTriggered = new Set(state.triggeredNodeIndexes)
      nextTriggered.delete(index)
      const nextDismissed = new Set(state.dismissedNodeIndexes)
      nextDismissed.add(index)
      return {
        triggeredNodeIndexes: nextTriggered,
        dismissedNodeIndexes: nextDismissed,
      }
    })
  },
  replayInteraction: (index) => {
    set((state) => {
      const nextTriggered = new Set(state.triggeredNodeIndexes)
      const nextDismissed = new Set(state.dismissedNodeIndexes)
      nextTriggered.delete(index)
      nextDismissed.delete(index)
      nextTriggered.add(index)
      return {
        triggeredNodeIndexes: nextTriggered,
        dismissedNodeIndexes: nextDismissed,
        currentSentence: [],
      }
    })
  },
  resetSentence: () => set({ currentSentence: [] }),
  pushWord: (word) => {
    set((state) => ({ currentSentence: [...state.currentSentence, word] }))
  },
  clearSentence: () => set({ currentSentence: [] }),
  clearInteractions: () =>
    set({
      triggeredNodeIndexes: new Set(),
      dismissedNodeIndexes: new Set(),
      currentSentence: [],
    }),
}))
