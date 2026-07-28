import type { ProductionRepository } from "../api/productionClient";
import type {
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunSummaryV1,
  ProductionRunViewV1,
  PublicConditionV1,
} from "../contracts/v1/production";
import {
  loadDashboardEmptyFixture,
  loadDurationWarningFixture,
  loadIdentityBlockedFixture,
  loadPlanOnlySuccessFixture,
  loadPlanningFailureFixture,
  loadRenderDisabledCapabilitiesFixture,
} from "../fixtures/v1/fixtureManifest";

export type MockScenario = "empty" | "completed" | "warning" | "blocked" | "failed";
type RunScenario = Exclude<MockScenario, "empty">;

function runForScenario(scenario: RunScenario): ProductionRunViewV1 {
  const base = loadPlanOnlySuccessFixture();
  if (scenario === "completed") {
    return base;
  }
  if (scenario === "warning") {
    return {
      ...base,
      run_id: "mock-plan-warning-01",
      status: "COMPLETED_WITH_WARNINGS",
      warnings: loadDurationWarningFixture(),
    };
  }
  const condition: PublicConditionV1 =
    scenario === "blocked"
      ? loadIdentityBlockedFixture()
      : {
          code: loadPlanningFailureFixture().code,
          title: "Planning failed",
          detail: loadPlanningFailureFixture().message,
          severity: "ERROR",
          scene_ids: [],
          beat_ids: [],
        };
  return {
    ...base,
    run_id: scenario === "blocked" ? "mock-plan-blocked-01" : "mock-plan-failed-01",
    status: scenario === "blocked" ? "BLOCKED" : "FAILED",
    current_stage: scenario === "blocked" ? "identity_eligibility" : "canonical_story_planning",
    story_plan: null,
    timeline: null,
    warnings: {
      schema_version: 1,
      warning_count: 0,
      conditions: [condition],
    },
  };
}

export const registeredMockRunIds = Object.freeze([
  "mock-plan-2026-01",
  "mock-plan-warning-01",
  "mock-plan-blocked-01",
  "mock-plan-failed-01",
] as const);

type RegisteredMockRunId = (typeof registeredMockRunIds)[number];

const registeredRunFactories: Readonly<
  Record<RegisteredMockRunId, () => ProductionRunViewV1>
> = Object.freeze({
  "mock-plan-2026-01": () => runForScenario("completed"),
  "mock-plan-warning-01": () => runForScenario("warning"),
  "mock-plan-blocked-01": () => runForScenario("blocked"),
  "mock-plan-failed-01": () => runForScenario("failed"),
});

const scenarioRunIds: Readonly<Record<RunScenario, RegisteredMockRunId>> = Object.freeze({
  completed: "mock-plan-2026-01",
  warning: "mock-plan-warning-01",
  blocked: "mock-plan-blocked-01",
  failed: "mock-plan-failed-01",
});

function registeredRun(runId: string): ProductionRunViewV1 | null {
  if (!Object.hasOwn(registeredRunFactories, runId)) {
    return null;
  }
  return registeredRunFactories[runId as RegisteredMockRunId]();
}

function summaryFromRun(run: ProductionRunViewV1): ProductionRunSummaryV1 {
  return {
    run_id: run.run_id,
    execution_mode: run.execution_mode,
    status: run.status,
    current_stage: run.current_stage,
    warning_count: run.warnings.warning_count,
    created_at: run.created_at,
    updated_at: run.updated_at,
  };
}

export class MockProductionRepository implements ProductionRepository {
  readonly #delayMs: number;

  constructor(delayMs = 0) {
    this.#delayMs = delayMs;
  }

  async #wait(): Promise<void> {
    if (this.#delayMs > 0) {
      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, this.#delayMs);
      });
    }
  }

  async createPlanOnlyRun(
    _request: PlanOnlyCreateRequestV1,
  ): Promise<PlanOnlyCreateResultV1> {
    await this.#wait();
    return {
      schema_version: 1,
      run: registeredRun("mock-plan-2026-01"),
      error: null,
    };
  }

  async getDashboard(scenario: MockScenario = "empty"): Promise<ProductionDashboardViewV1> {
    await this.#wait();
    if (scenario === "empty") {
      return loadDashboardEmptyFixture();
    }
    const run = registeredRun(scenarioRunIds[scenario]);
    if (run === null) {
      throw new Error("Registered mock run is unavailable");
    }
    return {
      schema_version: 1,
      runs: [summaryFromRun(run)],
    };
  }

  async getCapabilities(): Promise<ProductionCapabilitiesV1> {
    await this.#wait();
    return loadRenderDisabledCapabilitiesFixture();
  }

  async getHealth(): Promise<PlanOnlyHealthV1> {
    await this.#wait();
    return {
      schema_version: 1,
      status: "ok",
      contract_version: "v1",
      plan_only_available: true,
      render_available: false,
    };
  }

  async getRun(runId: string): Promise<ProductionRunViewV1 | null> {
    await this.#wait();
    return registeredRun(runId);
  }
}

export const mockProductionRepository = new MockProductionRepository();
