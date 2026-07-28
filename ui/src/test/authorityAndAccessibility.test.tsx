import { cleanup, render, screen, within } from "@testing-library/react";
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

    const timeline = screen.getByRole("table");
    expect(within(timeline).getAllByText("4.25 s")).toHaveLength(6);
    expect(within(timeline).getAllByText("8.75 s")).toHaveLength(2);
    expect(screen.getAllByText("35 s").length).toBeGreaterThanOrEqual(2);
  });

  it("shows unavailable server values without calculating replacements", async () => {
    renderRoute("/production/runs/mock-plan-2026-01");
    await screen.findByRole("table");

    expect(screen.getAllByText("Not available").length).toBeGreaterThanOrEqual(8);
    expect(screen.getByText("PLANNED")).toBeInTheDocument();
    expect(screen.queryByText("MEASURED")).not.toBeInTheDocument();
  });
});

describe("accessibility foundations", () => {
  it("provides named controls, disabled-mode reasons, and one main landmark", async () => {
    renderRoute("/production/new");
    await screen.findByRole("heading", { name: "Start with a clear plan" });

    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByLabelText("Topic or narration")).toBeEnabled();
    expect(screen.getByLabelText("Aspect ratio")).toBeDisabled();
    expect(screen.getByLabelText("Narration voice")).toBeDisabled();
    const aspectRatio = screen.getByLabelText("Aspect ratio");
    const voice = screen.getByLabelText("Narration voice");
    const planOnly = screen.getByRole("radio", { name: /PLAN_ONLY/ });
    const fullRender = screen.getByRole("radio", { name: /FULL_RENDER/ });
    expect(planOnly).toBeEnabled();
    expect(planOnly).toBeChecked();
    expect(aspectRatio).toHaveAccessibleDescription(
      "Locked by the current production contract.",
    );
    expect(voice).toHaveAccessibleDescription(
      "No topic-production voice contract is available.",
    );
    expect(planOnly).toHaveAccessibleDescription(
      "Canonical planning only. Rendering remains unavailable.",
    );
    expect(fullRender).toHaveAccessibleDescription(
      "Locked until backend, synthetic closure, and live canary approval.",
    );
    expect(
      new Set([
        aspectRatio.getAttribute("aria-describedby"),
        voice.getAttribute("aria-describedby"),
        planOnly.getAttribute("aria-describedby"),
        fullRender.getAttribute("aria-describedby"),
      ]).size,
    ).toBe(4);
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
