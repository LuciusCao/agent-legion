import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ListPage } from "./ListPage";

const mockApi = vi.fn();
vi.mock("../api", () => ({
  api: (...args: any[]) => mockApi(...args),
}));

describe("ListPage", () => {
  beforeEach(() => {
    mockApi.mockReset();
  });

  it("renders list page", () => {
    mockApi.mockResolvedValueOnce({ videos: [] });
    render(
      <MemoryRouter>
        <ListPage />
      </MemoryRouter>
    );
    expect(screen.getByText("知识点")).toBeInTheDocument();
  });

  it("filters list by content type when tab changes", async () => {
    mockApi.mockResolvedValueOnce({
      videos: [
        { id: "v1", title: "知识视频A", content_type: "knowledge", external_id: "k1", status: "completed", current_phase: "package", error_message: "" },
        { id: "v2", title: "题目视频B", content_type: "question", external_id: "q1", status: "completed", current_phase: "package", error_message: "" },
      ],
    });
    render(
      <MemoryRouter>
        <ListPage />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("知识视频A")).toBeInTheDocument();
    });
    expect(screen.queryByText("题目视频B")).not.toBeInTheDocument();

    const tabs = document.querySelector("md-tabs") as HTMLElement & { activeTabIndex: number };
    act(() => {
      tabs.activeTabIndex = 1;
      tabs.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await waitFor(() => {
      expect(screen.queryByText("知识视频A")).not.toBeInTheDocument();
    });
    expect(screen.getByText("题目视频B")).toBeInTheDocument();
  });
});
