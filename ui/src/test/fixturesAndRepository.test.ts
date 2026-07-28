import { afterEach, describe, expect, it, vi } from "vitest";

import { createCanonicalJsonFixture } from "../fixtures/v1/fixtureAuthority";
import {
  loadDurationWarningFixture,
  loadPlanOnlySuccessFixture,
  loadRenderDisabledCapabilitiesFixture,
  syntheticFixtureNotice,
} from "../fixtures/v1/fixtureManifest";
import {
  MockProductionRepository,
  registeredMockRunIds,
} from "../mock/mockProductionRepository";

afterEach(() => {
  vi.unstubAllGlobals();
});

async function requireRegisteredRun(
  repository: MockProductionRepository,
  runId: string,
) {
  const run = await repository.getRun(runId);
  if (run === null) {
    throw new Error(`Expected registered run: ${runId}`);
  }
  return run;
}

describe("deterministic fixture contract", () => {
  it("uses fixed identities, timestamps, and canonical ordering", () => {
    const run = loadPlanOnlySuccessFixture();
    expect(run.run_id).toBe("mock-plan-2026-01");
    expect(run.created_at).toBe("2026-01-15T08:00:00Z");
    expect(run.updated_at).toBe("2026-01-15T08:00:04Z");
    expect(run.story_plan?.scenes.map((scene) => scene.scene_id)).toEqual([
      "scene_01",
      "scene_02",
      "scene_03",
      "scene_04",
      "scene_05",
      "scene_06",
      "scene_07",
      "scene_08",
    ]);
    expect(run.timeline?.rows.map((row) => row.scene_id)).toEqual([
      "scene_01",
      "scene_02",
      "scene_03",
      "scene_04",
      "scene_05",
      "scene_06",
      "scene_07",
      "scene_08",
    ]);
    expect(loadDurationWarningFixture().conditions.map((item) => item.code)).toEqual([
      "OUTSIDE_DURATION_TARGET_WARNING",
    ]);
    expect(syntheticFixtureNotice).toContain("does not claim current backend");
  });

  it("returns deeply equal detached values without fetch", async () => {
    const fetchSentinel = vi.spyOn(globalThis, "fetch");
    const repository = new MockProductionRepository();
    const first = await requireRegisteredRun(repository, "mock-plan-2026-01");
    const second = await requireRegisteredRun(repository, "mock-plan-2026-01");

    expect(first).toEqual(second);
    expect(first).not.toBe(second);
    expect(first.story_plan).not.toBe(second.story_plan);
    expect(first.timeline?.rows).not.toBe(second.timeline?.rows);

    const mutable = first as unknown as {
      story_plan: {
        scenes: Array<{ scene_id: string }>;
        semantic_beats: Array<{ beat_id: string }>;
      };
      timeline: { rows: Array<{ scene_id: string }> };
      warnings: {
        warning_count: number;
        conditions: Array<{ code: string }>;
      };
      render_readiness: { ready: boolean; reason_codes: string[] };
    };
    mutable.story_plan.scenes.reverse();
    mutable.story_plan.semantic_beats[0]!.beat_id = "beat_99";
    mutable.timeline.rows[0]!.scene_id = "scene_99";
    mutable.warnings.warning_count = 99;
    mutable.warnings.conditions.push({
      code: "MUTATED",
    });
    mutable.render_readiness.ready = true;
    mutable.render_readiness.reason_codes.length = 0;

    const later = await requireRegisteredRun(repository, "mock-plan-2026-01");
    expect(later.story_plan?.scenes[0]?.scene_id).toBe("scene_01");
    expect(later.story_plan?.semantic_beats[0]?.beat_id).toBe("beat_01");
    expect(later.timeline?.rows[0]?.scene_id).toBe("scene_01");
    expect(later.warnings).toEqual({
      schema_version: 1,
      warning_count: 0,
      conditions: [],
    });
    expect(later.render_readiness).toEqual({
      ready: false,
      reason_codes: ["FULL_RENDER_LOCKED"],
    });
    expect(fetchSentinel).not.toHaveBeenCalled();
  });

  it("returns only explicitly registered run IDs with deterministic status", async () => {
    const repository = new MockProductionRepository();
    expect((await repository.getDashboard()).runs).toEqual([]);
    const expected = [
      ["mock-plan-2026-01", "COMPLETED"],
      ["mock-plan-warning-01", "COMPLETED_WITH_WARNINGS"],
      ["mock-plan-blocked-01", "BLOCKED"],
      ["mock-plan-failed-01", "FAILED"],
    ] as const;
    expect(registeredMockRunIds).toEqual(expected.map(([runId]) => runId));
    for (const [runId, status] of expected) {
      const run = await requireRegisteredRun(repository, runId);
      expect(run.run_id).toBe(runId);
      expect(run.status).toBe(status);
    }
  });

  it.each(["arbitrary-run", "not-registered-warning", "not-registered-failed"])(
    "does not infer a scenario for unknown ID %s",
    async (runId) => {
      const repository = new MockProductionRepository();

      expect(await repository.getRun(runId)).toBeNull();
      expect(await repository.getRun(runId)).toBeNull();
    },
  );

  it("isolates warning conditions and capabilities from caller mutation", async () => {
    const repository = new MockProductionRepository();
    const warning = await requireRegisteredRun(repository, "mock-plan-warning-01");
    const capabilities = await repository.getCapabilities();
    const mutableWarning = warning as unknown as {
      warnings: { conditions: Array<{ code: string }> };
    };
    const mutableCapabilities = capabilities as unknown as {
      full_render_enabled: boolean;
      render_lock_reason_codes: string[];
    };
    mutableWarning.warnings.conditions[0]!.code = "MUTATED";
    mutableCapabilities.full_render_enabled = true;
    mutableCapabilities.render_lock_reason_codes.length = 0;

    expect(
      (await repository.getRun("mock-plan-warning-01"))?.warnings.conditions[0]?.code,
    ).toBe("OUTSIDE_DURATION_TARGET_WARNING");
    expect(await repository.getCapabilities()).toEqual(
      loadRenderDisabledCapabilitiesFixture(),
    );
  });

  it("rejects unsupported non-JSON values at the canonical fixture boundary", () => {
    expect(() => createCanonicalJsonFixture({ value: new Date(0) })).toThrow(
      "contains a non-JSON object",
    );
    expect(() => createCanonicalJsonFixture({ value: Number.NaN })).toThrow(
      "must contain only finite JSON numbers",
    );
    expect(() => createCanonicalJsonFixture({ value: [, "sparse"] })).toThrow(
      "contains a sparse non-JSON array",
    );
  });

  it("performs no network activity for repository operations", async () => {
    const fetchSentinel = vi.fn();
    const xhrSentinel = vi.fn();
    const webSocketSentinel = vi.fn();
    const eventSourceSentinel = vi.fn();
    const sendBeaconSentinel = vi.fn();
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
    const repository = new MockProductionRepository();

    await repository.getDashboard();
    await repository.getCapabilities();
    await repository.getRun("mock-plan-2026-01");
    await repository.getRun("unknown");

    expect(fetchSentinel).not.toHaveBeenCalled();
    expect(xhrSentinel).not.toHaveBeenCalled();
    expect(webSocketSentinel).not.toHaveBeenCalled();
    expect(eventSourceSentinel).not.toHaveBeenCalled();
    expect(sendBeaconSentinel).not.toHaveBeenCalled();
  });
});
