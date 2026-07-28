import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { ProductionRepository } from "../api/productionClient";
import { validateTimelineAccess } from "../api/timelineValidation";
import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  TimelineAccessV1,
  TimelineCollectionV1,
  TimelineSegmentV1,
} from "../contracts/v1/production";
import { MockProductionRepository } from "../mock/mockProductionRepository";

const runId = "plan-0123456789abcdef0123";

function segment(
  position: 1 | 2,
  overrides: Partial<TimelineSegmentV1> = {},
): TimelineSegmentV1 {
  const start = position === 1 ? 0 : 4_000;
  const duration = 4_000;
  const end = start + duration;
  const sourceStart = position === 1 ? 0 : 8;
  const sourceEnd = position === 1 ? 8 : 16;
  return {
    schema_version: 1,
    segment_id: `timeline-segment-000${position}`,
    segment_revision_id: `timeline-segment-revision-000${position}`,
    segment_revision_number: 1,
    position,
    run_id: runId,
    story_revision_id: "revision-0001",
    narration_source_sha256: "a".repeat(64),
    scene_plan_collection_revision_id: "scene-plan-revision-0001",
    scene_id: `scene_0${position}`,
    scene_revision_id: `scene-revision-0001-0${position}`,
    visual_collection_revision_id: `visual-collection-revision-000${position}`,
    accepted_candidate_id: `visual-candidate-000${position}-01`,
    accepted_candidate_revision_id: `visual-candidate-revision-000${position}`,
    candidate_artifact_sha256: "b".repeat(64),
    composition_collection_revision_id: "composition-collection-revision-0001",
    composition_id: `scene-composition-000${position}`,
    composition_revision_id: `composition-revision-000${position}`,
    semantic_beat_id: `beat_0${position}`,
    source_coverage: { start: sourceStart, end: sourceEnd },
    canonical_narration_segment:
      position === 1 ? "A calm." : "A deliberate ritual.",
    scene_planned_duration_ms: duration,
    duration_ms: duration,
    start_ms: start,
    end_ms: end,
    transition_in_intent: "CUT",
    transition_in_ms: 0,
    transition_out_intent: "CUT",
    transition_out_ms: 0,
    effective_visible_duration_ms: duration,
    narration_alignment: {
      alignment_id: `narration-alignment-000${position}`,
      canonical_narration_segment:
        position === 1 ? "A calm." : "A deliberate ritual.",
      source_coverage: { start: sourceStart, end: sourceEnd },
      semantic_beat_id: `beat_0${position}`,
      timeline_start_ms: start,
      timeline_end_ms: end,
      planned_window_start_ms: start,
      planned_window_end_ms: end,
      status: "ALIGNED",
      source_coverage_valid: true,
      warning_codes: [],
      blocker_codes: [],
      measured_audio_alignment_available: false,
      tts_alignment_available: false,
    },
    motion_intent_summary: "STATIC",
    continuity_codes: ["identity_continuity_required"],
    warning_codes: [],
    blocker_codes: [],
    status: "VALID",
    note: null,
    changed_fields: [],
    superseded_reason: null,
    created_at: `2026-03-01T00:00:0${position}Z`,
    timeline_contract_version: "timeline_planning_v1",
    accepted_for_execution_review: false,
    timeline_execution_authority: false,
    narration_generation_capability: false,
    tts_capability: false,
    audio_generation_capability: false,
    renderer_execution_authority: false,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
    ...overrides,
  };
}

function collection(
  segments: readonly TimelineSegmentV1[] = [segment(1), segment(2)],
  overrides: Partial<TimelineCollectionV1> = {},
): TimelineCollectionV1 {
  const planned = segments.reduce((sum, item) => sum + item.duration_ms, 0);
  const overlap = segments
    .slice(0, -1)
    .reduce((sum, item) => sum + item.transition_out_ms, 0);
  return {
    schema_version: 1,
    collection_id: "timeline-collection-0001",
    collection_revision_id: "timeline-collection-revision-0001",
    collection_revision_number: 1,
    run_id: runId,
    story_revision_id: "revision-0001",
    narration_source_sha256: "a".repeat(64),
    source_authority_sha256: "c".repeat(64),
    scene_plan_collection_revision_id: "scene-plan-revision-0001",
    composition_collection_revision_id: "composition-collection-revision-0001",
    segments,
    total_segment_count: segments.length,
    total_planned_duration_ms: planned,
    total_transition_overlap_ms: overlap,
    effective_timeline_duration_ms: planned - overlap,
    target_min_ms: 1,
    target_max_ms: 38_000,
    narration_coverage_valid: true,
    ordering_valid: true,
    duration_valid: true,
    transition_valid: true,
    warning_segment_count: 0,
    blocked_segment_count: 0,
    overall_ready_for_execution_review: true,
    status: "VALID",
    accepted_for_execution_review: false,
    accepted_timeline_revision_id: null,
    review_history: [],
    created_at: "2026-03-01T00:00:01Z",
    process_local: true,
    full_render_enabled: false,
    timeline_execution_authority: false,
    narration_generation_capability: false,
    tts_capability: false,
    audio_generation_capability: false,
    renderer_execution_authority: false,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
    ...overrides,
  };
}

function access(current: TimelineCollectionV1 | null): TimelineAccessV1 {
  return {
    schema_version: 1,
    run_id: runId,
    editable: current !== null,
    initialization_authorized: true,
    blocker_codes: [],
    current_story_revision_id: "revision-0001",
    current_scene_plan_collection_revision_id: "scene-plan-revision-0001",
    current_composition_collection_revision_id:
      "composition-collection-revision-0001",
    collection: current,
    process_local: true,
    full_render_enabled: false,
    timeline_execution_authority: false,
    narration_generation_capability: false,
    tts_capability: false,
    audio_generation_capability: false,
    renderer_execution_authority: false,
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
  };
}

function repositoryWith(
  methods: Partial<ProductionRepository>,
): ProductionRepository {
  return Object.assign(new MockProductionRepository(), methods);
}

function renderWorkspace(repository: ProductionRepository) {
  return render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={[`/production/runs/${runId}/timeline`]}>
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("timeline planning workspace", () => {
  it("initializes explicitly from accepted composition authority", async () => {
    const initialize = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: access(collection()),
      collection: null,
      error: null,
    });
    renderWorkspace(
      repositoryWith({
        getTimelineAccess: vi.fn().mockResolvedValue(access(null)),
        initializeTimeline: initialize,
      }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Initialize timeline" }),
    );
    await screen.findByRole("heading", { name: "Timeline planning preview" });
    expect(initialize).toHaveBeenCalledWith(
      runId,
      "composition-collection-revision-0001",
    );
  });

  it("shows exact integer timing, narration, and false authority without media controls", async () => {
    renderWorkspace(
      repositoryWith({
        getTimelineAccess: vi.fn().mockResolvedValue(access(collection())),
        getTimelineSegmentHistory: vi.fn().mockResolvedValue({
          schema_version: 1,
          run_id: runId,
          segment_id: "timeline-segment-0001",
          revisions: [segment(1)],
        }),
      }),
    );
    expect(
      await screen.findByText(/Text\/source and planned timing alignment only/),
    ).toBeInTheDocument();
    expect(screen.getByText("A calm.")).toBeInTheDocument();
    expect(screen.getByText(/Read-only source offsets: 0–8/)).toBeInTheDocument();
    expect(screen.getAllByText("Not granted")).toHaveLength(3);
    expect(screen.queryByRole("audio")).not.toBeInTheDocument();
    expect(screen.queryByRole("video")).not.toBeInTheDocument();
    expect(screen.queryByText(/export/i)).not.toBeInTheDocument();
  });

  it("preserves failed edits and resets locally without autosave", async () => {
    const save = vi.fn().mockRejectedValue(new Error("failure"));
    renderWorkspace(
      repositoryWith({
        getTimelineAccess: vi.fn().mockResolvedValue(access(collection())),
        getTimelineSegmentHistory: vi.fn().mockResolvedValue(null),
        saveTimelineSegment: save,
      }),
    );
    const start = await screen.findByLabelText("Window start (milliseconds)");
    fireEvent.change(start, { target: { value: "100" } });
    expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Save segment revision" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "ended without a safe response",
    );
    expect(start).toHaveValue(100);
    await userEvent.click(
      screen.getByRole("button", { name: "Reset unsaved changes" }),
    );
    expect(start).toHaveValue(0);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("confirms segment switches when dirty", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWorkspace(
      repositoryWith({
        getTimelineAccess: vi.fn().mockResolvedValue(access(collection())),
        getTimelineSegmentHistory: vi.fn().mockResolvedValue(null),
      }),
    );
    fireEvent.change(
      await screen.findByLabelText("Window start (milliseconds)"),
      { target: { value: "100" } },
    );
    await userEvent.click(screen.getByText("timeline-segment-0002"));
    expect(window.confirm).toHaveBeenCalled();
    expect(screen.getByText("A calm.")).toBeInTheDocument();
  });

  it("accepts only for execution review and keeps execution disabled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const accepted = collection(undefined, {
      collection_revision_id: "timeline-collection-revision-0002",
      collection_revision_number: 2,
      status: "ACCEPTED_FOR_EXECUTION_REVIEW",
      accepted_for_execution_review: true,
      accepted_timeline_revision_id: "timeline-collection-revision-0002",
    });
    const review = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: null,
      collection: accepted,
      error: null,
    });
    renderWorkspace(
      repositoryWith({
        getTimelineAccess: vi.fn().mockResolvedValue(access(collection())),
        getTimelineSegmentHistory: vi.fn().mockResolvedValue(null),
        reviewTimeline: review,
      }),
    );
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Accept for execution review",
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByText(/Accepted for execution review only/),
      ).toBeInTheDocument(),
    );
    expect(review).toHaveBeenCalled();
  });

  it.each([
    ["execution authority", (value: TimelineAccessV1) => {
      (value as { timeline_execution_authority: boolean }).timeline_execution_authority =
        true;
    }],
    ["boolean timing", (value: TimelineAccessV1) => {
      (value.collection!.segments.at(0)! as { start_ms: unknown }).start_ms = true;
    }],
    ["fractional timing", (value: TimelineAccessV1) => {
      (value.collection!.segments.at(0)! as { duration_ms: number }).duration_ms =
        4_000.5;
    }],
    ["arithmetic mismatch", (value: TimelineAccessV1) => {
      (value.collection!.segments.at(0)! as { end_ms: number }).end_ms = 3_999;
    }],
    ["window inversion", (value: TimelineAccessV1) => {
      (
        value.collection!.segments.at(0)!.narration_alignment as {
          planned_window_start_ms: number;
        }
      ).planned_window_start_ms = 4_000;
    }],
  ])("rejects malformed %s responses", (_label, mutate) => {
    const payload = structuredClone(access(collection()));
    mutate(payload);
    expect(() => validateTimelineAccess(payload)).toThrow();
  });
});
