import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { AgentPanel } from "./AgentPanel";
import { useUiStore } from "../stores/uiStore";

const mockApi = vi.fn();
vi.mock("../api", () => ({
  api: (...args: any[]) => mockApi(...args),
}));

describe("AgentPanel", () => {
  beforeEach(() => {
    mockApi.mockReset();
    useUiStore.setState({
      agents: [],
      addDialogOpen: false,
      addContentType: "knowledge",
      rerunDialogOpen: false,
      deleteDialogOpen: false,
      toast: null,
      workerPaused: false,
    });
  });

  it("renders empty state when no agents", () => {
    render(<AgentPanel />);
    expect(screen.getByText(/暂无运行中的 Agent/)).toBeInTheDocument();
  });

  it("loads worker status and toggles queue scheduling", async () => {
    mockApi.mockResolvedValueOnce({ paused: false }).mockResolvedValueOnce({ paused: true });

    const { container } = render(<AgentPanel />);

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith("/api/worker/status");
    });
    expect(screen.getByText("队列调度中")).toBeInTheDocument();

    const switchEl = container.querySelector("md-switch") as HTMLElement & { selected?: boolean };
    expect(switchEl).toBeInTheDocument();
    expect(switchEl).toHaveAttribute("selected", "true");

    await act(async () => {
      switchEl.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mockApi).toHaveBeenCalledWith("/api/worker/pause", { method: "POST" });
    expect(useUiStore.getState().workerPaused).toBe(true);
    expect(screen.getByText("已暂停队列调度")).toBeInTheDocument();
    expect(switchEl).not.toHaveAttribute("selected");
  });
});
