import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import {
  BackendProductionRepository,
  ProductionBackendUnavailableError,
  type ProductionRepository,
} from "../api/productionClient";
import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunViewV1,
} from "../contracts/v1/production";
import { loadPlanOnlySuccessFixture } from "../fixtures/v1/fixtureManifest";

afterEach(cleanup);

const runId = "plan-0123456789abcdefabcd";

function planOnlyRun(): ProductionRunViewV1 {
  return {
    ...loadPlanOnlySuccessFixture(),
    run_id: runId,
    execution_mode: "PLAN_ONLY",
    status: "PLANNED",
    current_stage: "planned",
  };
}

function lockedCapabilities(): ProductionCapabilitiesV1 {
  return {
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
}

class TestRepository implements ProductionRepository {
  readonly create = vi.fn<
    (request: PlanOnlyCreateRequestV1) => Promise<PlanOnlyCreateResultV1>
  >();
  readonly dashboard = vi.fn<() => Promise<ProductionDashboardViewV1>>();
  readonly capabilities = vi.fn<() => Promise<ProductionCapabilitiesV1>>();
  readonly health = vi.fn<() => Promise<PlanOnlyHealthV1>>();
  readonly run = vi.fn<(runId: string) => Promise<ProductionRunViewV1 | null>>();

  constructor() {
    this.capabilities.mockResolvedValue(lockedCapabilities());
    this.health.mockResolvedValue({
      schema_version: 1,
      status: "ok",
      contract_version: "v1",
      plan_only_available: true,
      render_available: false,
    });
    this.dashboard.mockResolvedValue({ schema_version: 1, runs: [] });
    this.run.mockResolvedValue(null);
  }

  createPlanOnlyRun(request: PlanOnlyCreateRequestV1) {
    return this.create(request);
  }

  getDashboard() {
    return this.dashboard();
  }

  getCapabilities() {
    return this.capabilities();
  }

  getHealth() {
    return this.health();
  }

  getRun(requestedRunId: string) {
    return this.run(requestedRunId);
  }
}

function renderRoute(path: string, repository: ProductionRepository) {
  return render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

async function reachReview(
  content = "Learning to make room for uncertainty",
) {
  await userEvent.type(await screen.findByLabelText("Topic content"), content);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(
    await screen.findByRole("heading", { name: "Review StoryPlan request" }),
  ).toBeVisible();
}

describe("PLAN_ONLY backend UI integration", () => {
  it("creates a backend-backed plan and navigates by exact run ID", async () => {
    const repository = new TestRepository();
    const run = planOnlyRun();
    repository.create.mockResolvedValue({ schema_version: 1, run, error: null });
    repository.run.mockImplementation(async (requested) =>
      requested === runId ? run : null,
    );
    renderRoute("/production/new", repository);

    await reachReview();
    await userEvent.click(screen.getByRole("button", { name: "Create StoryPlan" }));

    expect(await screen.findByRole("heading", { name: "Production run" })).toBeVisible();
    expect(screen.getByText(runId)).toBeVisible();
    expect(repository.create).toHaveBeenCalledWith({
      schema_version: 1,
      input_mode: "TOPIC",
      source_content: "Learning to make room for uncertainty",
      language: "en",
      character_scope: "recurring_female",
      requested_scene_count: 8,
    });
    expect(repository.run).toHaveBeenCalledWith(runId);
    expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  });

  it("shows a native loading state while planning is pending", async () => {
    const repository = new TestRepository();
    let resolveCreate:
      | ((value: PlanOnlyCreateResultV1) => void)
      | undefined;
    repository.create.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCreate = resolve;
        }),
    );
    renderRoute("/production/new", repository);

    await reachReview();
    await userEvent.click(screen.getByRole("button", { name: "Create StoryPlan" }));

    expect(screen.getByText(/Planning/).closest('[role="status"]')).not.toBeNull();
    resolveCreate?.({
      schema_version: 1,
      run: planOnlyRun(),
      error: null,
    });
  });

  it.each([
    ["BLOCKED", "Planning blocked", "Identity scope is unsupported."],
    ["FAILED", "Planning failed", "Planning could not complete."],
  ] as const)("shows typed %s state", async (status, title, message) => {
    const repository = new TestRepository();
    repository.create.mockResolvedValue({
      schema_version: 1,
      run: null,
      error: {
        schema_version: 1,
        request_id: "request-0123456789abcdef",
        status,
        code: status === "BLOCKED" ? "UNSUPPORTED_IDENTITY_POLICY" : "PLANNING_FAILED",
        message,
        retryable: false,
        field_errors: [],
        details: {},
      },
    });
    renderRoute("/production/new", repository);

    await reachReview();
    await userEvent.click(screen.getByRole("button", { name: "Create StoryPlan" }));

    expect(await screen.findByRole("heading", { name: title })).toBeVisible();
    expect(screen.getByText(message)).toBeVisible();
  });

  it("distinguishes malformed backend responses", async () => {
    const repository = new BackendProductionRepository({
      async request(path) {
        if (path.endsWith("/capabilities")) {
          return { status: 200, payload: lockedCapabilities() };
        }
        return { status: 200, payload: { schema_version: 1, run: null, error: null } };
      },
    });
    renderRoute("/production/new", repository);

    await reachReview();
    await userEvent.click(screen.getByRole("button", { name: "Create StoryPlan" }));

    expect(
      await screen.findByRole("heading", { name: "Invalid backend response" }),
    ).toBeVisible();
  });

  it("shows backend-unavailable state without exposing internals", async () => {
    const repository = new TestRepository();
    repository.create.mockRejectedValue(new ProductionBackendUnavailableError());
    renderRoute("/production/new", repository);

    await reachReview();
    await userEvent.click(screen.getByRole("button", { name: "Create StoryPlan" }));

    expect(await screen.findByRole("heading", { name: "Backend unavailable" })).toBeVisible();
    expect(screen.queryByText(/stack|traceback/i)).not.toBeInTheDocument();
  });

  it("renders the backend dashboard list and exact review link", async () => {
    const repository = new TestRepository();
    const run = planOnlyRun();
    repository.dashboard.mockResolvedValue({
      schema_version: 1,
      runs: [
        {
          run_id: run.run_id,
          execution_mode: "PLAN_ONLY",
          status: "PLANNED",
          current_stage: "planned",
          warning_count: 0,
          created_at: run.created_at,
          updated_at: run.updated_at,
        },
      ],
    });
    renderRoute("/production", repository);

    expect(await screen.findByText(runId)).toBeVisible();
    expect(screen.getByRole("link", { name: "Review plan" })).toHaveAttribute(
      "href",
      `/production/runs/${runId}`,
    );
  });

  it("preserves accessible unknown-run and render-lock states", async () => {
    const repository = new TestRepository();
    repository.run.mockResolvedValue(null);
    renderRoute("/production/runs/unknown", repository);

    expect(
      await screen.findByRole("heading", { name: "Production run not found" }),
    ).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  });
});
