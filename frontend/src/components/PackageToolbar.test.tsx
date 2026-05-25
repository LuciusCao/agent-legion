import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PackageToolbar } from "./PackageToolbar";

vi.mock("../stores/videoStore", () => ({
  useVideoStore: vi.fn(() => ({
    selectedIds: new Set(),
    togglePackageSelectMode: vi.fn(),
    selectPackageAll: vi.fn(),
    selectPackageUnpacked: vi.fn(),
    batchPackage: vi.fn(),
    fetchVideos: vi.fn(),
  })),
}));

describe("PackageToolbar", () => {
  it("renders action buttons", () => {
    render(<PackageToolbar />);
    expect(screen.getByText("全选")).toBeInTheDocument();
    expect(screen.getByText("仅选未打包")).toBeInTheDocument();
    expect(screen.getByText("取消选择")).toBeInTheDocument();
    expect(screen.getByText("退出")).toBeInTheDocument();
  });
});
