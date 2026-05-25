import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NodePanel } from "./NodePanel";
import { useDetailStore } from "../stores/detailStore";

describe("NodePanel", () => {
  beforeEach(() => {
    useDetailStore.setState({
      artifacts: {
        interactions: [
          {
            trigger_time: 90,
            instruction: "Test instruction",
            type: "example_practice",
            options: [{ id: "a", text: "Option A", is_distractor: false }],
          },
        ],
      },
      triggeredNodeIndexes: new Set(),
    });
  });

  it("clicking node card calls onSeek and replayInteraction", () => {
    const onSeek = vi.fn();
    const replayInteraction = vi.fn();

    render(<NodePanel onSeek={onSeek} replayInteraction={replayInteraction} />);
    const card = screen.getByText("Test instruction").closest(".node-card");
    fireEvent.click(card!);
    expect(onSeek).toHaveBeenCalledWith(90);
    expect(replayInteraction).toHaveBeenCalledWith(0);
  });
});
