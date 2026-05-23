import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AddDialog } from "./AddDialog";
import { useUiStore } from "../stores/uiStore";

describe("AddDialog", () => {
  it("renders dialog with correct title", () => {
    useUiStore.setState({ addDialogOpen: true });
    render(<AddDialog />);
    expect(screen.getByText("添加资源")).toBeInTheDocument();
    useUiStore.setState({ addDialogOpen: false });
  });
});
