import type { ProductionRepository } from "../api/productionClient";
import type {
  AcceptStoryPlanRequestV1,
  PlanOnlyCreateRequestV1,
  PlanOnlyCreateResultV1,
  PlanOnlyHealthV1,
  ProductionCapabilitiesV1,
  ProductionDashboardViewV1,
  ProductionRunSummaryV1,
  ProductionRunViewV1,
  PublicConditionV1,
  ReplanRequestV1,
  StoryPlanReviewOperationResultV1,
  StoryPlanReviewViewV1,
  StoryPlanRevisionHistoryV1,
  StoryPlanRevisionV1,
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

function mockRevision(run: ProductionRunViewV1): StoryPlanRevisionV1 | null {
  if (run.story_plan === null || run.timeline === null) {
    return null;
  }
  let cursor = 0;
  const beats = run.story_plan.semantic_beats.map((beat) => {
    const segmentStart = run.story_plan?.narration_text.indexOf(
      beat.narration_segment,
      cursor,
    ) ?? cursor;
    const end = segmentStart + Array.from(beat.narration_segment).length;
    const narrationSegment = Array.from(run.story_plan?.narration_text ?? "")
      .slice(cursor, end)
      .join("");
    const start = cursor;
    cursor = end;
    return {
      ...beat,
      source_span: { start, end },
      narration_segment: narrationSegment,
      transition_intent: "Continue the canonical emotional progression",
    };
  });
  return {
    schema_version: 1,
    run_id: run.run_id,
    revision_id: "revision-0001",
    revision_number: 1,
    created_at: run.created_at,
    reasons: ["INITIAL_PLAN"],
    custom_note: null,
    target_duration_seconds: run.story_plan.target_duration_seconds,
    beat_count: beats.length,
    story_plan: {
      ...run.story_plan,
      requested_scene_count: run.story_plan.scenes.length as 7 | 8,
      topic_intent: run.story_plan.topic,
      semantic_beats: beats,
      planner_metadata: {
        planner_id: "mock.plan_only",
        planner_version: "v1",
        deterministic: true,
        external_calls: 0,
        production_eligible: false,
        story_planning_authorized: true,
      },
    },
    timeline: run.timeline,
    warnings: run.warnings,
    duration_assessment: {
      policy_id: "mvp_emotional_duration_32_38_v1",
      status: "IN_TARGET",
      reason_code: "DURATION_IN_TARGET",
      value_authority: "PLANNED",
      target_duration_seconds: run.story_plan.target_duration_seconds,
      target_min_seconds: 32,
      target_max_seconds: 38,
      semantic_beat_total_seconds: run.story_plan.target_duration_seconds,
      scene_planning_total_seconds: run.story_plan.target_duration_seconds,
    },
    identity_scope: {
      requested_scope: "recurring_female",
      supported: true,
      eligibility_status: "SUPPORTED_RECURRING_FEMALE",
      recurring_female_required: true,
      anonymous_background_people_allowed: false,
      identity_continuity_required: true,
      visual_identity_verified: false,
      blocking_reason_codes: [],
    },
  };
}

function mockReviewForRun(
  run: ProductionRunViewV1,
  status: StoryPlanReviewViewV1["review_status"] = "UNREVIEWED",
): StoryPlanReviewViewV1 | null {
  const revision = mockRevision(run);
  if (revision === null) {
    return null;
  }
  const accepted = status === "ACCEPTED_FOR_SCENE_PLANNING";
  return {
    schema_version: 1,
    run_id: run.run_id,
    review_status: status,
    current_revision_id: revision.revision_id,
    accepted_revision_id: accepted ? revision.revision_id : null,
    revision_history: [
      {
        revision_id: revision.revision_id,
        revision_number: revision.revision_number,
        created_at: revision.created_at,
        reasons: revision.reasons,
        target_duration_seconds: revision.target_duration_seconds,
        beat_count: revision.beat_count,
      },
    ],
    current_revision: revision,
    scene_planning_accepted: accepted,
    render_authority: false,
    media_capability: false,
    process_local: true,
  };
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

  async getStoryPlanReview(runId: string): Promise<StoryPlanReviewViewV1 | null> {
    await this.#wait();
    const run = registeredRun(runId);
    return run === null ? null : mockReviewForRun(run);
  }

  async acceptStoryPlan(
    runId: string,
    _request: AcceptStoryPlanRequestV1,
  ): Promise<StoryPlanReviewOperationResultV1> {
    await this.#wait();
    const run = registeredRun(runId);
    const review =
      run === null
        ? null
        : mockReviewForRun(run, "ACCEPTED_FOR_SCENE_PLANNING");
    return {
      schema_version: 1,
      review,
      revision: review?.current_revision ?? null,
      error: null,
    };
  }

  async requestStoryPlanReplan(
    runId: string,
    _request: ReplanRequestV1,
  ): Promise<StoryPlanReviewOperationResultV1> {
    await this.#wait();
    const run = registeredRun(runId);
    const review =
      run === null ? null : mockReviewForRun(run, "REPLAN_REQUESTED");
    return {
      schema_version: 1,
      review,
      revision: review?.current_revision ?? null,
      error: null,
    };
  }

  async getStoryPlanRevisions(
    runId: string,
  ): Promise<StoryPlanRevisionHistoryV1 | null> {
    const run = registeredRun(runId);
    const review = run === null ? null : mockReviewForRun(run);
    return review === null
      ? null
      : {
          schema_version: 1,
          run_id: runId,
          current_revision_id: review.current_revision_id,
          revisions: review.revision_history,
        };
  }

  async getStoryPlanRevision(
    runId: string,
    revisionId: string,
  ): Promise<StoryPlanRevisionV1 | null> {
    const run = registeredRun(runId);
    const revision = run === null ? null : mockRevision(run);
    return revision?.revision_id === revisionId ? revision : null;
  }
}

export const mockProductionRepository = new MockProductionRepository();
