import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ListPage } from "./ListPage";

vi.mock("../api", () => ({
  api: vi.fn(() => Promise.resolve({ videos: [] })),
}));

describe("ListPage", () => {
  it("renders page title", () => {
    render(
      <MemoryRouter>
        <ListPage />
      </MemoryRouter>
    );
    expect(screen.getByText("Video Hive")).toBeInTheDocument();
  });
});
