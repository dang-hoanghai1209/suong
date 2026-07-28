import { describe, expect, it, vi } from "vitest";

import {
  BackendProductionRepository,
  defineNativeFetchClient,
  ProductionContractError,
  type PlanOnlyTransport,
} from "../api/productionClient";
import type {
  PlanOnlyCreateRequestV1,
  ProductionRunViewV1,
} from "../contracts/v1/production";
import { loadPlanOnlySuccessFixture } from "../fixtures/v1/fixtureManifest";
import { mockProductionRepository } from "../mock/mockProductionRepository";

const runId = "plan-0123456789abcdefabcd";

function backendRun(): ProductionRunViewV1 {
  return {
    ...loadPlanOnlySuccessFixture(),
    run_id: runId,
    execution_mode: "PLAN_ONLY",
    status: "PLANNED",
    current_stage: "planned",
  };
}

const request: PlanOnlyCreateRequestV1 = {
  schema_version: 1,
  input_mode: "TOPIC",
  source_content: "Learning to make room for uncertainty",
  language: "en",
  character_scope: "recurring_female",
  requested_scene_count: 8,
};

class StaticTransport implements PlanOnlyTransport {
  readonly calls: Array<{ path: string; init?: RequestInit }> = [];
  readonly #responses: Readonly<Record<string, { status: number; payload: unknown }>>;

  constructor(responses: Readonly<Record<string, { status: number; payload: unknown }>>) {
    this.#responses = responses;
  }

  async request(path: string, init?: RequestInit) {
    this.calls.push(init === undefined ? { path } : { path, init });
    return this.#responses[`${init?.method ?? "GET"} ${path}`] ?? {
      status: 404,
      payload: null,
    };
  }
}

function capabilities() {
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

describe("backend production repository", () => {
  it("uses the exact same-origin health path and validates render unavailability", async () => {
    const transport = new StaticTransport({
      "GET /api/v1/plan-only/health": {
        status: 200,
        payload: {
          schema_version: 1,
          status: "ok",
          contract_version: "v1",
          plan_only_available: true,
          render_available: false,
        },
      },
    });

    await expect(new BackendProductionRepository(transport).getHealth()).resolves.toEqual({
      schema_version: 1,
      status: "ok",
      contract_version: "v1",
      plan_only_available: true,
      render_available: false,
    });
    expect(transport.calls[0]?.path).toBe("/api/v1/plan-only/health");
  });

  it.each([
    ["plan_only_available", "true"],
    ["plan_only_available", false],
    ["render_available", true],
    ["render_available", 0],
    ["contract_version", "v2"],
  ])("rejects malformed health field %s=%j", async (field, malformed) => {
    const transport = new StaticTransport({
      "GET /api/v1/plan-only/health": {
        status: 200,
        payload: {
          schema_version: 1,
          status: "ok",
          contract_version: "v1",
          plan_only_available: true,
          render_available: false,
          [field]: malformed,
        },
      },
    });

    await expect(
      new BackendProductionRepository(transport).getHealth(),
    ).rejects.toBeInstanceOf(ProductionContractError);
  });

  it("uses exact local paths and validates detached creation output", async () => {
    const payload = {
      schema_version: 1,
      run: backendRun(),
      error: null,
    };
    const transport = new StaticTransport({
      "POST /api/v1/plan-only/runs": { status: 200, payload },
    });
    const repository = new BackendProductionRepository(transport);

    const result = await repository.createPlanOnlyRun(request);
    expect(result.run?.run_id).toBe(runId);
    expect(transport.calls).toHaveLength(1);
    expect(transport.calls[0]?.path).toBe("/api/v1/plan-only/runs");
    expect(JSON.parse(String(transport.calls[0]?.init?.body))).toEqual(request);

    (result.run as { status: string }).status = "FAILED";
    expect(payload.run.status).toBe("PLANNED");
  });

  it("rejects malformed run ordering and identity mismatches", async () => {
    const malformed = structuredClone(backendRun()) as unknown as {
      story_plan: { semantic_beats: Array<{ order: number }> };
    };
    malformed.story_plan.semantic_beats[0]!.order = 8;
    const transport = new StaticTransport({
      [`GET /api/v1/plan-only/runs/${runId}`]: {
        status: 200,
        payload: malformed,
      },
    });
    await expect(
      new BackendProductionRepository(transport).getRun(runId),
    ).rejects.toBeInstanceOf(ProductionContractError);
  });

  it("returns null only for exact 404 lookup", async () => {
    const transport = new StaticTransport({});
    const repository = new BackendProductionRepository(transport);

    await expect(repository.getRun("unknown")).resolves.toBeNull();
    expect(transport.calls[0]?.path).toBe("/api/v1/plan-only/runs/unknown");
  });

  it("validates dashboard fields and deterministic order from the backend", async () => {
    const run = backendRun();
    const transport = new StaticTransport({
      "GET /api/v1/plan-only/runs": {
        status: 200,
        payload: {
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
        },
      },
    });

    const dashboard = await new BackendProductionRepository(transport).getDashboard();
    expect(dashboard.runs.map((item) => item.run_id)).toEqual([runId]);
  });

  it.each(["true", "false", 1, null, [], {}])(
    "rejects malformed capability gate %j",
    async (malformed) => {
      const transport = new StaticTransport({
        "GET /api/v1/plan-only/capabilities": {
          status: 200,
          payload: { ...capabilities(), full_render_enabled: malformed },
        },
      });

      await expect(
        new BackendProductionRepository(transport).getCapabilities(),
      ).rejects.toBeInstanceOf(ProductionContractError);
    },
  );

  it("accepts exact boolean capabilities with string-only reason codes", async () => {
    const transport = new StaticTransport({
      "GET /api/v1/plan-only/capabilities": {
        status: 200,
        payload: capabilities(),
      },
    });

    const result = await new BackendProductionRepository(transport).getCapabilities();
    expect(result.full_render_enabled).toBe(false);
    expect(result.render_lock_reason_codes).toEqual(["PLAN_ONLY_NO_RENDER_AUTHORITY"]);
  });

  it("refuses external URLs before invoking fetch", async () => {
    const fetchSentinel = vi.fn();
    const transport = defineNativeFetchClient(fetchSentinel as unknown as typeof fetch);

    await expect(transport.request("https://external.example/runs")).rejects.toBeInstanceOf(
      ProductionContractError,
    );
    expect(fetchSentinel).not.toHaveBeenCalled();
  });

  it("refuses unrecognized local operations before invoking fetch", async () => {
    const fetchSentinel = vi.fn();
    const transport = defineNativeFetchClient(fetchSentinel as unknown as typeof fetch);

    await expect(
      transport.request("/api/v1/plan-only/render", { method: "POST" }),
    ).rejects.toBeInstanceOf(ProductionContractError);
    expect(fetchSentinel).not.toHaveBeenCalled();
  });
});

describe("StoryPlan review repository boundary", () => {
  it("uses exact review routes and returns detached validated authority", async () => {
    const review = await mockProductionRepository.getStoryPlanReview(
      "mock-plan-2026-01",
    );
    expect(review).not.toBeNull();
    const transport = new StaticTransport({
      [`GET /api/v1/plan-only/runs/${runId}/review`]: {
        status: 200,
        payload: { ...structuredClone(review), run_id: runId, current_revision: {
          ...structuredClone(review?.current_revision),
          run_id: runId,
        } },
      },
    });
    const repository = new BackendProductionRepository(transport);

    const result = await repository.getStoryPlanReview(runId);

    expect(result?.review_status).toBe("UNREVIEWED");
    expect(result?.render_authority).toBe(false);
    expect(result?.media_capability).toBe(false);
    expect(transport.calls[0]?.path).toBe(
      `/api/v1/plan-only/runs/${runId}/review`,
    );
    if (result !== null) {
      (result.current_revision.story_plan.semantic_beats as unknown as Array<{ beat_id: string }>)[0]!
        .beat_id = "mutated";
    }
    expect(
      (
        transport as unknown as {
          readonly calls: unknown[];
        }
      ).calls,
    ).toHaveLength(1);
    expect(review?.current_revision.story_plan.semantic_beats[0]?.beat_id).toBe(
      "beat_01",
    );
  });

  it.each([
    ["duplicate beat", (payload: Record<string, any>) => {
      payload.current_revision.story_plan.semantic_beats[1].beat_id = "beat_01";
    }],
    ["out-of-order beat", (payload: Record<string, any>) => {
      payload.current_revision.story_plan.semantic_beats[0].order = 8;
    }],
    ["forged render authority", (payload: Record<string, any>) => {
      payload.render_authority = true;
    }],
    ["non-boolean media authority", (payload: Record<string, any>) => {
      payload.media_capability = "false";
    }],
    ["out-of-order revision history", (payload: Record<string, any>) => {
      payload.revision_history[0].revision_number = 2;
    }],
  ])("rejects %s", async (_label, mutate) => {
    const review = await mockProductionRepository.getStoryPlanReview(
      "mock-plan-2026-01",
    );
    const payload = structuredClone(review) as unknown as Record<string, any>;
    payload.run_id = runId;
    payload.current_revision.run_id = runId;
    mutate(payload);
    const transport = new StaticTransport({
      [`GET /api/v1/plan-only/runs/${runId}/review`]: {
        status: 200,
        payload,
      },
    });

    await expect(
      new BackendProductionRepository(transport).getStoryPlanReview(runId),
    ).rejects.toBeInstanceOf(ProductionContractError);
  });

  it("posts only bounded acceptance and replan payloads to exact local routes", async () => {
    const base = await mockProductionRepository.getStoryPlanReview(
      "mock-plan-2026-01",
    );
    expect(base).not.toBeNull();
    const accepted = {
      ...structuredClone(base),
      run_id: runId,
      review_status: "ACCEPTED_FOR_SCENE_PLANNING",
      accepted_revision_id: "revision-0001",
      scene_planning_accepted: true,
      current_revision: {
        ...structuredClone(base?.current_revision),
        run_id: runId,
      },
    };
    const operation = {
      schema_version: 1,
      review: accepted,
      revision: accepted.current_revision,
      error: null,
    };
    const transport = new StaticTransport({
      [`POST /api/v1/plan-only/runs/${runId}/review/accept`]: {
        status: 200,
        payload: operation,
      },
      [`POST /api/v1/plan-only/runs/${runId}/revisions`]: {
        status: 200,
        payload: operation,
      },
    });
    const repository = new BackendProductionRepository(transport);
    const acceptRequest = {
      schema_version: 1 as const,
      current_revision_id: "revision-0001",
    };
    const replanRequest = {
      schema_version: 1 as const,
      base_revision_id: "revision-0001",
      feedback: ["story_focus_incorrect"] as const,
      custom_note: null,
    };

    await repository.acceptStoryPlan(runId, acceptRequest);
    await repository.requestStoryPlanReplan(runId, replanRequest);

    expect(transport.calls.map((call) => call.path)).toEqual([
      `/api/v1/plan-only/runs/${runId}/review/accept`,
      `/api/v1/plan-only/runs/${runId}/revisions`,
    ]);
    expect(JSON.parse(String(transport.calls[0]?.init?.body))).toEqual(
      acceptRequest,
    );
    expect(JSON.parse(String(transport.calls[1]?.init?.body))).toEqual(
      replanRequest,
    );
  });
});
