import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { InteractionOverlay } from "./InteractionOverlay";

describe("InteractionOverlay", () => {
  it("lets sentence-building nodes append words", () => {
    const onWordClick = vi.fn();

    render(
      <InteractionOverlay
        node={{
          instruction: "连词成句",
          answer: ["hello", "world"],
        }}
        currentSentence={[]}
        onWordClick={onWordClick}
        onReset={vi.fn()}
        onContinue={vi.fn()}
      />,
    );

    screen.getByText("hello").click();

    expect(onWordClick).toHaveBeenCalledWith("hello");
  });
});
