import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { App } from "../app/App";
import globalCss from "../styles/global.css?inline";

afterEach(cleanup);

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("authority display", () => {
  it("preserves explicit scene and timeline order and values", async () => {
    renderRoute("/production/runs/mock-plan-2026-01");
    await screen.findByRole("heading", { name: "Production run" });

    const sceneList = screen.getByRole("heading", { name: "Scenes" }).closest("section");
    expect(sceneList).not.toBeNull();
    const sceneIds = within(sceneList as HTMLElement)
      .getAllByText(/^scene_/)
      .map((element) => element.textContent);
    expect(sceneIds).toEqual([
      "scene_01",
      "scene_02",
      "scene_03",
      "scene_04",
      "scene_05",
      "scene_06",
      "scene_07",
      "scene_08",
    ]);

    expect(within(sceneList as HTMLElement).getAllByText(/Timeline .* s/)).toHaveLength(8);
    expect(within(sceneList as HTMLElement).getByText("Timeline 0–4.25 s")).toBeVisible();
    expect(within(sceneList as HTMLElement).getByText("Timeline 30.5–35 s")).toBeVisible();
    expect(screen.getAllByText("35 s").length).toBeGreaterThanOrEqual(2);
  });

  it("shows unavailable server values without calculating replacements", async () => {
    renderRoute("/production/runs/mock-plan-2026-01");
    await screen.findByRole("heading", { name: "Duration assessment" });

    expect(screen.getByText("Estimated narration duration").nextElementSibling).toHaveTextContent(
      "Not supplied",
    );
    expect(screen.getByText(/No measured narration/)).toBeVisible();
    expect(screen.getByText("PLANNED")).toBeInTheDocument();
    expect(screen.queryByText("MEASURED")).not.toBeInTheDocument();
  });
});

describe("accessibility foundations", () => {
  it("provides named controls, disabled-mode reasons, and one main landmark", async () => {
    renderRoute("/production/new");
    await screen.findByRole("heading", { name: "Start with a clear plan" });

    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByLabelText("Topic content")).toBeEnabled();
    await userEvent.type(screen.getByLabelText("Topic content"), "A careful topic");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    const planOnly = screen.getByRole("radio", { name: /PLAN_ONLY/ });
    const fullRender = screen.getByRole("radio", { name: /FULL_RENDER/ });
    expect(planOnly).toBeEnabled();
    expect(planOnly).toBeChecked();
    expect(planOnly).toHaveAccessibleDescription(
      "Creates a StoryPlan only. No narration, media, or video is generated.",
    );
    expect(fullRender).toHaveAccessibleDescription(
      "Disabled because this workflow has no render authority.",
    );
    expect(
      new Set([
        planOnly.getAttribute("aria-describedby"),
        fullRender.getAttribute("aria-describedby"),
      ]).size,
    ).toBe(2);
  });

  it("defines visible focus and reduced-motion behavior", () => {
    expect(globalCss).toContain(":focus-visible");
    expect(globalCss).toContain("outline: 2px solid var(--color-focus)");
    expect(globalCss).toContain("@media (prefers-reduced-motion: reduce)");
  });

  it("uses text and an icon in status presentation", async () => {
    renderRoute("/production/runs/mock-plan-warning-01");
    const status = await screen.findByText("Completed with warnings");

    expect(status.closest(".status-badge")).toHaveTextContent("!");
    expect(status.closest(".status-badge")).toHaveTextContent("Completed with warnings");
  });
});
