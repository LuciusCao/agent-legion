import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { AgentPanel } from "./AgentPanel";

describe("AgentPanel", () => {
  it("renders nothing when no agents", () => {
    const { container } = render(<AgentPanel />);
    expect(container.firstChild).toBeNull();
  });
});
