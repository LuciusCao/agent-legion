import { describe, it, expect, beforeEach } from "vitest";
import { useInteractionStore } from "./interactionStore";

describe("interactionStore", () => {
  beforeEach(() => {
    useInteractionStore.setState({
      triggeredNodeIndexes: new Set(),
      dismissedNodeIndexes: new Set(),
      currentSentence: [],
    });
  });

  it("triggers interaction", () => {
    useInteractionStore.getState().triggerInteraction(0);
    expect(useInteractionStore.getState().triggeredNodeIndexes.has(0)).toBe(true);
  });

  it("dismisses interaction", () => {
    useInteractionStore.getState().triggerInteraction(0);
    useInteractionStore.getState().dismissInteraction(0);
    expect(useInteractionStore.getState().triggeredNodeIndexes.has(0)).toBe(false);
    expect(useInteractionStore.getState().dismissedNodeIndexes.has(0)).toBe(true);
  });

  it("replayInteraction resets dismissed and re-triggers node", () => {
    useInteractionStore.getState().triggerInteraction(0);
    useInteractionStore.getState().dismissInteraction(0);
    expect(useInteractionStore.getState().triggeredNodeIndexes.has(0)).toBe(false);
    expect(useInteractionStore.getState().dismissedNodeIndexes.has(0)).toBe(true);

    useInteractionStore.getState().replayInteraction(0);
    expect(useInteractionStore.getState().triggeredNodeIndexes.has(0)).toBe(true);
    expect(useInteractionStore.getState().dismissedNodeIndexes.has(0)).toBe(false);
    expect(useInteractionStore.getState().currentSentence).toEqual([]);
  });

  it("pushWord adds to currentSentence", () => {
    useInteractionStore.getState().pushWord("hello");
    useInteractionStore.getState().pushWord("world");
    expect(useInteractionStore.getState().currentSentence).toEqual(["hello", "world"]);
  });

  it("clearSentence resets currentSentence", () => {
    useInteractionStore.setState({ currentSentence: ["hello"] });
    useInteractionStore.getState().clearSentence();
    expect(useInteractionStore.getState().currentSentence).toEqual([]);
  });

  it("clearInteractions resets trigger, dismiss, and sentence state", () => {
    useInteractionStore.setState({
      triggeredNodeIndexes: new Set([0]),
      dismissedNodeIndexes: new Set([1]),
      currentSentence: ["hello"],
    });

    useInteractionStore.getState().clearInteractions();

    expect(useInteractionStore.getState().triggeredNodeIndexes.size).toBe(0);
    expect(useInteractionStore.getState().dismissedNodeIndexes.size).toBe(0);
    expect(useInteractionStore.getState().currentSentence).toEqual([]);
  });
});
