import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { App } from "../app/App";
import type { ProductionRepository } from "../api/productionClient";
import { validateCompositionAccess } from "../api/compositionValidation";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  CompositionAccessV1,
  CompositionCollectionV1,
  SceneCompositionV1,
} from "../contracts/v1/production";
import { MockProductionRepository } from "../mock/mockProductionRepository";

const runId = "plan-0123456789abcdef0123";
const artifactUrl =
  "/api/v1/plan-only/runs/plan-0123456789abcdef0123/scene-plan/scenes/scene_01/visual-candidates/visual-candidate-0001-01/artifact";

function composition(overrides: Partial<SceneCompositionV1> = {}): SceneCompositionV1 {
  const geometry = {
    x: 0,
    y: 0,
    width: 1,
    height: 1,
    anchor_x: 0.5,
    anchor_y: 0.5,
    scale: 1,
    rotation_degrees: 0,
    opacity: 1,
  };
  return {
    schema_version: 1,
    composition_id: "scene-composition-0001",
    composition_revision_id: "composition-revision-0001",
    composition_revision_number: 1,
    position: 1,
    run_id: runId,
    story_revision_id: "revision-0001",
    scene_plan_collection_revision_id: "scene-plan-revision-0001",
    scene_id: "scene_01",
    scene_revision_id: "scene-revision-0001-01",
    visual_collection_revision_id: "visual-collection-revision-0001",
    accepted_candidate_id: "visual-candidate-0001-01",
    accepted_candidate_revision_id: "visual-candidate-revision-0001",
    accepted_candidate_sha256: "a".repeat(64),
    artifact_url: artifactUrl,
    source_width: 576,
    source_height: 1024,
    source_mime: "image/png",
    source_coverage: { start: 0, end: 8 },
    semantic_beat_id: "beat_01",
    planned_duration_seconds: 4,
    aspect_ratio: "9:16",
    composition_contract_version: "composition_planning_v1",
    status: "VALID",
    fit_mode: "COVER",
    crop: { x: 0, y: 0, width: 1, height: 1 },
    placement: geometry,
    safe_margins: {
      top: 0.08,
      bottom: 0.12,
      left: 0.06,
      right: 0.06,
      title_safe: 0.1,
      subtitle_safe: 0.16,
    },
    layers: [
      {
        layer_id: "composition-layer-00",
        layer_type: "ACCEPTED_VISUAL",
        z_order: 0,
        enabled: true,
        geometry,
        opacity: 1,
        color: null,
      },
    ],
    motion_intent: {
      mode: "STATIC",
      start_scale: 1,
      end_scale: 1,
      start_anchor_x: 0.5,
      start_anchor_y: 0.5,
      end_anchor_x: 0.5,
      end_anchor_y: 0.5,
    },
    transition_intent: "CUT",
    transition_duration_seconds: 0,
    note: null,
    validation: {
      valid: true,
      blocker_codes: [],
      warning_codes: [],
      information_codes: ["HUMAN_COMPOSITION_REVIEW_REQUIRED"],
    },
    continuity_codes: ["identity_continuity_required"],
    changed_fields: [],
    review_history: [],
    accepted_for_timeline_planning: false,
    superseded_reason: null,
    created_at: "2026-02-01T00:00:01Z",
    render_authority: false,
    renderer_execution_authority: false,
    video_render_authority: false,
    timeline_execution_authority: false,
    final_media_capability: false,
    narration_generation_capability: false,
    tts_capability: false,
    ...overrides,
  };
}

function collection(item = composition()): CompositionCollectionV1 {
  return {
    schema_version: 1,
    collection_id: "composition-collection-0001",
    collection_revision_id: "composition-collection-revision-0001",
    collection_revision_number: 1,
    run_id: runId,
    story_revision_id: "revision-0001",
    scene_plan_collection_revision_id: "scene-plan-revision-0001",
    compositions: [item],
    missing_scene_ids: [],
    total_scene_count: 1,
    valid_composition_count: 1,
    warning_composition_count: 0,
    blocked_composition_count: 0,
    accepted_for_timeline_planning_count:
      item.accepted_for_timeline_planning ? 1 : 0,
    overall_ready_for_timeline_planning: item.accepted_for_timeline_planning,
    created_at: "2026-02-01T00:00:01Z",
    render_authority: false,
    renderer_execution_authority: false,
    video_render_authority: false,
    timeline_execution_authority: false,
    final_media_capability: false,
  };
}

function access(current: CompositionCollectionV1 | null): CompositionAccessV1 {
  return {
    schema_version: 1,
    run_id: runId,
    editable: current !== null,
    initialization_authorized: true,
    blocker_codes: [],
    current_story_revision_id: "revision-0001",
    current_scene_plan_collection_revision_id: "scene-plan-revision-0001",
    collection: current,
    process_local: true,
    render_authority: false,
    renderer_execution_authority: false,
    video_render_authority: false,
    timeline_execution_authority: false,
    final_media_capability: false,
    narration_generation_capability: false,
    tts_capability: false,
  };
}

function renderWorkspace(repository: ProductionRepository) {
  return render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={[`/production/runs/${runId}/compositions`]}>
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

function repositoryWith(
  methods: Partial<ProductionRepository>,
): ProductionRepository {
  return Object.assign(new MockProductionRepository(), methods);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("composition planning workspace", () => {
  it("initializes explicitly from accepted authority", async () => {
    const initialized = collection();
    const initialize = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: access(initialized),
      collection: null,
      error: null,
    });
    renderWorkspace(repositoryWith({
      getCompositionAccess: vi.fn().mockResolvedValue(access(null)),
      initializeCompositions: initialize,
    }));
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Initialize composition collection",
      }),
    );
    await screen.findByRole("heading", { name: "Planning preview" });
    expect(initialize).toHaveBeenCalledWith(runId, "scene-plan-revision-0001");
  });

  it.each([
    ["renderer authority", (value: CompositionAccessV1) => {
      (value as { renderer_execution_authority: boolean }).renderer_execution_authority = true;
    }],
    ["boolean geometry", (value: CompositionAccessV1) => {
      (value.collection!.compositions.at(0)!.placement as { x: unknown }).x = true;
    }],
    ["nonfinite geometry", (value: CompositionAccessV1) => {
      (value.collection!.compositions.at(0)!.placement as { x: number }).x = Number.NaN;
    }],
    ["duplicate layer order", (value: CompositionAccessV1) => {
      const base = value.collection!.compositions.at(0)!.layers.at(0)!;
      (value.collection!.compositions.at(0)!.layers as unknown[]).push({
        ...base,
        layer_id: "composition-layer-01",
        layer_type: "SAFE_COLOR_WASH",
        z_order: 0,
        color: "#112233",
      });
    }],
    ["external artifact URL", (value: CompositionAccessV1) => {
      (value.collection!.compositions.at(0)! as { artifact_url: string }).artifact_url =
        "https://example.invalid/image.png";
    }],
  ])("rejects malformed %s responses", (_label, mutate) => {
    const payload = structuredClone(access(collection()));
    mutate(payload);
    expect(() => validateCompositionAccess(payload)).toThrow();
  });

  it("shows bounded preview, safe zones, authority locks, and no execution controls", async () => {
    const view = renderWorkspace(repositoryWith({
      getCompositionAccess: vi.fn().mockResolvedValue(access(collection())),
      getCompositionHistory: vi.fn().mockResolvedValue({
        schema_version: 1,
        run_id: runId,
        scene_id: "scene_01",
        revisions: [composition()],
      }),
    }));
    expect(await screen.findByText("Browser geometry preview · not a rendered frame · not motion or color verification")).toBeInTheDocument();
    expect(screen.getByAltText("Accepted visual source visual-candidate-0001-01")).toHaveAttribute("src", artifactUrl);
    expect(screen.getAllByText("Not granted")).toHaveLength(3);
    expect(view.container.querySelectorAll(".safe-zone")).toHaveLength(2);
    expect(screen.queryByText(/export video/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/TTS/i)).not.toBeInTheDocument();
  });

  it("preserves edits through backend failure and resets without mutation", async () => {
    const save = vi.fn().mockRejectedValue(new Error("failure"));
    renderWorkspace(repositoryWith({
      getCompositionAccess: vi.fn().mockResolvedValue(access(collection())),
      getCompositionHistory: vi.fn().mockResolvedValue(null),
      saveComposition: save,
    }));
    const scale = await screen.findByLabelText("Scale");
    fireEvent.change(scale, { target: { value: "1.1" } });
    expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save revision" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "ended without a safe response",
    );
    expect(scale).toHaveValue(1.1);
    await userEvent.click(
      screen.getByRole("button", { name: "Reset unsaved changes" }),
    );
    expect(scale).toHaveValue(1);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("accepts for timeline planning only and handles preview failure", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const acceptedItem = composition({
      composition_revision_id: "composition-revision-0002",
      composition_revision_number: 2,
      status: "ACCEPTED_FOR_TIMELINE_PLANNING",
      accepted_for_timeline_planning: true,
    });
    const review = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: null,
      collection: collection(acceptedItem),
      error: null,
    });
    renderWorkspace(repositoryWith({
      getCompositionAccess: vi.fn().mockResolvedValue(access(collection())),
      getCompositionHistory: vi.fn().mockResolvedValue(null),
      reviewComposition: review,
    }));
    const image = await screen.findByAltText(
      "Accepted visual source visual-candidate-0001-01",
    );
    fireEvent.error(image);
    expect(screen.getByText("Accepted visual preview unavailable.")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Accept for timeline planning" }),
    );
    await waitFor(() =>
      expect(screen.getByText("Accepted for timeline planning only.")).toBeInTheDocument(),
    );
    expect(review).toHaveBeenCalled();
  });
});
