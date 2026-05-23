import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentPanel } from "./AgentPanel";

describe("AgentPanel", () => {
  it("renders empty state when no agents", () => {
    render(<AgentPanel />);
    expect(screen.getByText(/暂无运行中的 Agent/)).toBeInTheDocument();
  });
});
