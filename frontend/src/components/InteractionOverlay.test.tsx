import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { InteractionOverlay } from "./InteractionOverlay";

const baseProps = {
  currentSentence: [],
  onWordClick: vi.fn(),
  onReset: vi.fn(),
  onContinue: vi.fn(),
};

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

  it("renders example practice as a compact player card", () => {
    const onContinue = vi.fn();

    render(
      <InteractionOverlay
        {...baseProps}
        onContinue={onContinue}
        node={{
          type: "example_practice",
          trigger_time: 47,
          instruction: "先试做",
          hint: "用前面的方法先求一次。",
        }}
      />,
    );

    expect(screen.getByText("例题试做")).toBeInTheDocument();
    expect(screen.getByText("用前面的方法先求一次。")).toBeInTheDocument();

    fireEvent.click(screen.getByText("我已完成，继续"));

    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("selects summary options into an ordered preview", () => {
    render(
      <InteractionOverlay
        {...baseProps}
        node={{
          type: "interaction_summary",
          instruction: "按顺序选择",
          options: [
            { id: "a", text: "梳理条件", is_distractor: false },
            { id: "b", text: "找到关系", is_distractor: false },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "找到关系" }));
    fireEvent.click(screen.getByRole("button", { name: "梳理条件" }));

    const preview = screen.getByLabelText("已选排序预览");
    const items = within(preview).getAllByTestId("summary-order-item");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("找到关系"),
      expect.stringContaining("梳理条件"),
    ]);
  });

  it("reorders selected summary options by dragging preview items", () => {
    render(
      <InteractionOverlay
        {...baseProps}
        node={{
          type: "video_summary",
          instruction: "拖拽排序",
          options: [
            { id: "a", text: "第一步", is_distractor: false },
            { id: "b", text: "第二步", is_distractor: false },
            { id: "c", text: "第三步", is_distractor: false },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "第一步" }));
    fireEvent.click(screen.getByRole("button", { name: "第二步" }));
    fireEvent.click(screen.getByRole("button", { name: "第三步" }));

    const preview = screen.getByLabelText("已选排序预览");
    const before = within(preview).getAllByTestId("summary-order-item");

    fireEvent.dragStart(before[2]);
    fireEvent.dragEnter(before[0]);
    fireEvent.dragOver(before[0]);
    fireEvent.drop(before[0]);
    fireEvent.dragEnd(before[2]);

    const after = within(preview).getAllByTestId("summary-order-item");
    expect(after.map((item) => item.textContent)).toEqual([
      expect.stringContaining("第三步"),
      expect.stringContaining("第一步"),
      expect.stringContaining("第二步"),
    ]);
  });

  it("moves selected summary options with accessible controls", () => {
    render(
      <InteractionOverlay
        {...baseProps}
        node={{
          type: "video_summary",
          instruction: "调整排序",
          options: [
            { id: "a", text: "第一步", is_distractor: false },
            { id: "b", text: "第二步", is_distractor: false },
            { id: "c", text: "第三步", is_distractor: false },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "第一步" }));
    fireEvent.click(screen.getByRole("button", { name: "第二步" }));
    fireEvent.click(screen.getByRole("button", { name: "第三步" }));

    fireEvent.click(screen.getByRole("button", { name: "下移 第一步" }));

    const preview = screen.getByLabelText("已选排序预览");
    let items = within(preview).getAllByTestId("summary-order-item");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("第二步"),
      expect.stringContaining("第一步"),
      expect.stringContaining("第三步"),
    ]);

    fireEvent.click(screen.getByRole("button", { name: "上移 第一步" }));

    items = within(preview).getAllByTestId("summary-order-item");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("第一步"),
      expect.stringContaining("第二步"),
      expect.stringContaining("第三步"),
    ]);
  });

  it("drops dragged summary options before the target item when moving downward", () => {
    render(
      <InteractionOverlay
        {...baseProps}
        node={{
          type: "video_summary",
          instruction: "拖拽排序",
          options: [
            { id: "a", text: "第一步", is_distractor: false },
            { id: "b", text: "第二步", is_distractor: false },
            { id: "c", text: "第三步", is_distractor: false },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "第一步" }));
    fireEvent.click(screen.getByRole("button", { name: "第二步" }));
    fireEvent.click(screen.getByRole("button", { name: "第三步" }));

    const preview = screen.getByLabelText("已选排序预览");
    const before = within(preview).getAllByTestId("summary-order-item");

    fireEvent.dragStart(before[0]);
    fireEvent.dragEnter(before[2]);
    fireEvent.dragOver(before[2]);
    fireEvent.drop(before[2]);
    fireEvent.dragEnd(before[0]);

    const after = within(preview).getAllByTestId("summary-order-item");
    expect(after.map((item) => item.textContent)).toEqual([
      expect.stringContaining("第二步"),
      expect.stringContaining("第一步"),
      expect.stringContaining("第三步"),
    ]);
  });

  it("confirms summary interactions and continues playback", () => {
    const onContinue = vi.fn();

    render(
      <InteractionOverlay
        {...baseProps}
        onContinue={onContinue}
        node={{
          type: "interaction_summary",
          instruction: "确认继续",
          options: [{ id: "a", text: "梳理条件", is_distractor: false }],
        }}
      />,
    );

    fireEvent.click(screen.getByText("确认并继续"));

    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});
