import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("global styles", () => {
  it("keeps video status badges pill-shaped", () => {
    const css = readFileSync(join(process.cwd(), "src/styles.css"), "utf-8");
    expect(css).toMatch(/\.status-badge\s*\{[^}]*border-radius:\s*999px;/s);
  });

  it("keeps agent status pills pill-shaped", () => {
    const css = readFileSync(
      join(process.cwd(), "src/components/AgentPanel.module.css"),
      "utf-8",
    );
    expect(css).toMatch(/\.agent-pill\s*\{[^}]*border-radius:\s*999px;/s);
  });
});
