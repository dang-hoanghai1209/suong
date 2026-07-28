import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { App } from "../app/App";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("routing and shell", () => {
  it.each([
    ["/production", "Video plans"],
    ["/production/new", "Start with a clear plan"],
    ["/production/runs/mock-plan-2026-01", "Production run"],
    ["/missing", "Page not found"],
  ])("renders %s", async (path, heading) => {
    renderRoute(path);

    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
  });
});

describe("zero-network UI.1", () => {
  it.each([
    "/production",
    "/production/new",
    "/production/runs/mock-plan-2026-01",
    "/production/runs/arbitrary-run",
    "/not-found",
  ])("performs no network activity on %s", async (path) => {
    const fetchSentinel = vi.fn(() => {
      throw new Error("fetch is forbidden in MOCK");
    });
    const xhrSentinel = vi.fn(() => {
      throw new Error("XMLHttpRequest is forbidden in MOCK");
    });
    const webSocketSentinel = vi.fn(() => {
      throw new Error("WebSocket is forbidden in MOCK");
    });
    const eventSourceSentinel = vi.fn(() => {
      throw new Error("EventSource is forbidden in MOCK");
    });
    const sendBeaconSentinel = vi.fn(() => {
      throw new Error("sendBeacon is forbidden in MOCK");
    });
    const navigatorWithBeacon = Object.create(navigator) as Navigator;
    Object.defineProperty(navigatorWithBeacon, "sendBeacon", {
      configurable: true,
      value: sendBeaconSentinel,
    });
    vi.stubGlobal("fetch", fetchSentinel);
    vi.stubGlobal("XMLHttpRequest", xhrSentinel);
    vi.stubGlobal("WebSocket", webSocketSentinel);
    vi.stubGlobal("EventSource", eventSourceSentinel);
    vi.stubGlobal("navigator", navigatorWithBeacon);

    renderRoute(path);
    await screen.findByRole("main");

    expect(fetchSentinel).not.toHaveBeenCalled();
    expect(xhrSentinel).not.toHaveBeenCalled();
    expect(webSocketSentinel).not.toHaveBeenCalled();
    expect(eventSourceSentinel).not.toHaveBeenCalled();
    expect(sendBeaconSentinel).not.toHaveBeenCalled();
  });

  it("has no callable render action or render endpoint", async () => {
    renderRoute("/production/new");
    await screen.findByRole("heading", { name: "Start with a clear plan" });

    expect(screen.getByRole("radio", { name: /PLAN_ONLY/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /FULL_RENDER/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: /render video/i })).not.toBeInTheDocument();
  });

  it.each([
    "/production/runs/arbitrary-run",
    "/production/runs/not-registered-warning",
    "/production/runs/not-registered-failed",
  ])("renders an accessible explicit not-found state for %s", async (path) => {
    renderRoute(path);

    const heading = await screen.findByRole("heading", {
      level: 1,
      name: "Production run not found",
    });
    expect(heading.closest('[role="alert"]')).not.toBeNull();
    expect(
      screen.queryByText("Learning to make room for uncertainty"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Scenes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("The requested production run is not registered.")).toBeVisible();
  });
});
