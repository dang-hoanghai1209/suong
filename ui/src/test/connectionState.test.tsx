import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { ProductionRepository } from "../api/productionClient";
import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
} from "../contracts/v1/production";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const health: PlanOnlyHealthV1 = {
  schema_version: 1,
  status: "ok",
  contract_version: "v1",
  plan_only_available: true,
  render_available: false,
};

const capabilities: ProductionCapabilitiesV1 = {
  schema_version: 1,
  supported_execution_modes: ["PLAN_ONLY"],
  supported_input_modes: ["TOPIC"],
  supported_languages: ["vi", "en"],
  supported_aspect_ratios: ["9:16"],
  supported_visual_modes: ["illustrated_scene"],
  supported_character_scopes: [
    "recurring_female",
    "female_with_anonymous_background",
  ],
  backend_render_capability: false,
  synthetic_closure_passed: false,
  live_canary_passed: false,
  full_render_enabled: false,
  render_lock_reason_codes: ["PLAN_ONLY_NO_RENDER_AUTHORITY"],
};

class ConnectionRepository implements ProductionRepository {
  readonly getHealth = vi.fn<() => Promise<PlanOnlyHealthV1>>();

  async getCapabilities() {
    return capabilities;
  }

  async getDashboard() {
    return { schema_version: 1 as const, runs: [] };
  }

  async getRun(_runId: string) {
    return null;
  }

  async createPlanOnlyRun(
    _request: PlanOnlyCreateRequestV1,
  ): Promise<PlanOnlyCreateResultV1> {
    throw new Error("not used");
  }
}

function renderShell(repository: ProductionRepository, path = "/production") {
  return render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

describe("local backend connection state", () => {
  it("announces connecting and then connected PLAN_ONLY", async () => {
    const repository = new ConnectionRepository();
    let resolveHealth: ((value: PlanOnlyHealthV1) => void) | undefined;
    repository.getHealth.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveHealth = resolve;
        }),
    );
    renderShell(repository, "/production/new");

    expect(screen.getByText("Connecting").closest('[role="status"]')).not.toBeNull();
    resolveHealth?.(health);

    expect(await screen.findByText("Connected — PLAN_ONLY")).toBeVisible();
  });

  it("announces unavailability and retries without polling", async () => {
    const repository = new ConnectionRepository();
    repository.getHealth.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(health);
    renderShell(repository);

    expect(await screen.findByText("Backend unavailable")).toBeVisible();
    expect(repository.getHealth).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "Retry connection" }));

    expect(await screen.findByText("Connected — PLAN_ONLY")).toBeVisible();
    expect(repository.getHealth).toHaveBeenCalledTimes(2);
  });

  it("keeps FULL_RENDER disabled and creates no alternate transports", async () => {
    const repository = new ConnectionRepository();
    repository.getHealth.mockResolvedValue(health);
    const webSocket = vi.fn();
    const eventSource = vi.fn();
    const xmlHttpRequest = vi.fn();
    const sendBeacon = vi.fn();
    vi.stubGlobal("WebSocket", webSocket);
    vi.stubGlobal("EventSource", eventSource);
    vi.stubGlobal("XMLHttpRequest", xmlHttpRequest);
    const navigatorWithBeacon = Object.create(navigator) as Navigator;
    Object.defineProperty(navigatorWithBeacon, "sendBeacon", {
      configurable: true,
      value: sendBeacon,
    });
    vi.stubGlobal("navigator", navigatorWithBeacon);
    renderShell(repository, "/production/new");

    expect(await screen.findByText("Connected — PLAN_ONLY")).toBeVisible();
    expect(screen.getByRole("radio", { name: /FULL_RENDER/ })).toBeDisabled();
    expect(webSocket).not.toHaveBeenCalled();
    expect(eventSource).not.toHaveBeenCalled();
    expect(xmlHttpRequest).not.toHaveBeenCalled();
    expect(sendBeacon).not.toHaveBeenCalled();
  });
});
