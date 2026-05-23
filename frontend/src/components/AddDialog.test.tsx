import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AddDialog } from "./AddDialog";

describe("AddDialog", () => {
  it("renders dialog with correct title", () => {
    render(<AddDialog />);
    expect(screen.getByText("添加资源")).toBeInTheDocument();
  });
});
