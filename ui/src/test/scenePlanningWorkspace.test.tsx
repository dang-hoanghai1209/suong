import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  DuplicateSceneRequestV1,
  MergeScenesRequestV1,
  ReorderSceneRequestV1,
  RequestSceneRevisionV1,
  RestoreSceneRevisionRequestV1,
  SaveSceneRevisionRequestV1,
  ScenePlanAccessV1,
  ScenePlanOperationResultV1,
  ScenePlanSceneHistoryV1,
  SceneTransitionRequestV1,
  SplitSceneRequestV1,
  StoryPlanReviewViewV1,
} from "../contracts/v1/production";
import { loadPlanOnlySuccessFixture } from "../fixtures/v1/fixtureManifest";
import {
  MockProductionRepository,
  mockProductionRepository,
} from "../mock/mockProductionRepository";
import globalCss from "../styles/global.css?inline";

const runId = "mock-plan-2026-01";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function acceptedReview(): Promise<StoryPlanReviewViewV1> {
  const review = await mockProductionRepository.getStoryPlanReview(runId);
  if (!review) throw new Error("review missing");
  return {
    ...structuredClone(review),
    review_status: "ACCEPTED_FOR_SCENE_PLANNING",
    accepted_revision_id: review.current_revision_id,
    scene_planning_accepted: true,
  };
}

function accessFromReview(review: StoryPlanReviewViewV1): ScenePlanAccessV1 {
  const scenes = review.current_revision.story_plan.scenes.map((scene) => {
    const beat = review.current_revision.story_plan.semantic_beats.find(
      (item) => item.beat_id === scene.source_beat_id,
    )!;
    return {
      schema_version: 1 as const,
      run_id: runId,
      source_story_revision_id: review.current_revision_id,
      scene_id: scene.scene_id,
      scene_revision_id: `scene-revision-0001-${scene.order.toString().padStart(2, "0")}`,
      scene_revision_number: 1,
      order: scene.order,
      source_beat_id: scene.source_beat_id,
      source_coverage: { ...beat.source_span, overlap_draft: false },
      narration_segment: beat.narration_segment,
      objective: scene.meaning,
      emotional_intent: scene.emotional_tone.join(", "),
      environment: "",
      character_action: "",
      objects: [],
      composition_guidance: scene.visual_intent,
      continuity_notes: "",
      camera_motion_intent: "",
      transition_intent: beat.transition_intent,
      planned_duration_seconds: scene.planned_duration_seconds,
      planning_note: "",
      status: "VALID" as const,
      warning_codes: [],
      blocker_codes: [],
      continuity: {
        recurring_character_required: true,
        anonymous_background_allowed: false,
        identity_continuity_required: true as const,
        environment_continuity: "PLANNING_REQUIRED" as const,
        object_continuity: "PLANNING_REQUIRED" as const,
        visual_identity_verified: false as const,
      },
      created_at: review.current_revision.created_at,
    };
  });
  return {
    schema_version: 1,
    run_id: runId,
    editable: true,
    blocker_codes: [],
    collection: {
      schema_version: 1,
      run_id: runId,
      source_story_revision_id: review.current_revision_id,
      accepted_story_revision_id: review.current_revision_id,
      collection_revision_id: "scene-plan-revision-0001",
      collection_revision_number: 1,
      created_at: review.current_revision.created_at,
      reason: "INITIAL_DERIVATION",
      source_narration_length:
        review.current_revision.story_plan.narration_text.length,
      scenes,
      revision_history: [
        {
          collection_revision_id: "scene-plan-revision-0001",
          revision_number: 1,
          created_at: review.current_revision.created_at,
          reason: "INITIAL_DERIVATION",
          affected_scene_ids: scenes.map((scene) => scene.scene_id),
        },
      ],
      target_duration_seconds: 35,
      target_min_seconds: 32,
      target_max_seconds: 38,
      total_planned_duration_seconds: scenes.reduce(
        (sum, scene) => sum + scene.planned_duration_seconds,
        0,
      ),
      duration_valid: true,
      source_coverage_valid: true,
      ready_for_visual_planning: true,
      render_authority: false,
      media_capability: false,
      process_local: true,
    },
    render_authority: false,
    media_capability: false,
    process_local: true,
  };
}

class SceneRepository extends MockProductionRepository {
  review: StoryPlanReviewViewV1;
  access: ScenePlanAccessV1;
  readonly initialAccess: ScenePlanAccessV1;

  constructor(review: StoryPlanReviewViewV1, access = accessFromReview(review)) {
    super();
    this.review = review;
    this.access = access;
    this.initialAccess = structuredClone(access);
  }

  override async getRun(requested: string) {
    return requested === runId ? loadPlanOnlySuccessFixture() : null;
  }

  override async getStoryPlanReview(requested: string) {
    return requested === runId ? structuredClone(this.review) : null;
  }

  async getScenePlan(requested: string) {
    return requested === runId ? structuredClone(this.access) : null;
  }

  readonly saveSceneRevision = vi.fn(
    async (
      _runId: string,
      sceneId: string,
      request: SaveSceneRevisionRequestV1,
    ): Promise<ScenePlanOperationResultV1> => {
      const collection = this.access.collection!;
      const scenes = collection.scenes.map((scene) =>
        scene.scene_id === sceneId
          ? {
              ...scene,
              ...request.changes,
              scene_revision_id: "scene-revision-0002-01",
              scene_revision_number: 2,
            }
          : scene,
      );
      this.access = {
        ...this.access,
        collection: {
          ...collection,
          collection_revision_id: "scene-plan-revision-0002",
          collection_revision_number: 2,
          scenes,
          revision_history: [
            ...collection.revision_history,
            {
              collection_revision_id: "scene-plan-revision-0002",
              revision_number: 2,
              created_at: collection.created_at,
              reason: "SCENE_EDITED",
              affected_scene_ids: [sceneId],
            },
          ],
        },
      };
      return { schema_version: 1, access: structuredClone(this.access), error: null };
    },
  );

  readonly reorderScene = vi.fn(
    async (_runId: string, _request: ReorderSceneRequestV1) =>
      ({ schema_version: 1, access: this.access, error: null }) as const,
  );
  readonly splitScene = vi.fn(
    async (_runId: string, _request: SplitSceneRequestV1) =>
      ({ schema_version: 1, access: this.access, error: null }) as const,
  );
  readonly mergeScenes = vi.fn(
    async (_runId: string, _request: MergeScenesRequestV1) =>
      ({ schema_version: 1, access: this.access, error: null }) as const,
  );
  readonly duplicateScene = vi.fn(
    async (_runId: string, _request: DuplicateSceneRequestV1) =>
      ({ schema_version: 1, access: this.access, error: null }) as const,
  );
  readonly acceptScene = vi.fn(
    async (
      _runId: string,
      _sceneId: string,
      _request: SceneTransitionRequestV1,
    ) => ({ schema_version: 1, access: this.access, error: null }) as const,
  );
  readonly requestSceneRevision = vi.fn(
    async (
      _runId: string,
      _sceneId: string,
      _request: RequestSceneRevisionV1,
    ) => ({ schema_version: 1, access: this.access, error: null }) as const,
  );
  readonly restoreSceneRevision = vi.fn(
    async (
      _runId: string,
      _sceneId: string,
      _request: RestoreSceneRevisionRequestV1,
    ) => ({ schema_version: 1, access: this.access, error: null }) as const,
  );

  async getSceneHistory(
    _runId: string,
    sceneId: string,
  ): Promise<ScenePlanSceneHistoryV1 | null> {
    const scene = this.access.collection?.scenes.find(
      (item) => item.scene_id === sceneId,
    );
    const original = this.initialAccess.collection?.scenes.find(
      (item) => item.scene_id === sceneId,
    );
    return scene
      ? {
          schema_version: 1,
          run_id: runId,
          scene_id: sceneId,
          current_scene_revision_id: scene.scene_revision_id,
          revisions:
            original && original.scene_revision_id !== scene.scene_revision_id
              ? [original, scene]
              : [scene],
        }
      : null;
  }
}

function renderWorkspace(repository: SceneRepository) {
  return render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={[`/production/runs/${runId}/scenes`]}>
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

describe("PLAN_ONLY scene-planning workspace", () => {
  it("renders the exact accepted collection, editable fields, and authority locks", async () => {
    const repository = new SceneRepository(await acceptedReview());
    renderWorkspace(repository);
    expect(
      await screen.findByRole("heading", { name: "Scene planning workspace" }),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("navigation", { name: "Scene planning navigation" }),
      ).getAllByRole("listitem"),
    ).toHaveLength(8);
    expect(screen.getByLabelText("Scene objective")).toHaveValue(
      repository.access.collection?.scenes[0]?.objective,
    );
    expect(screen.getAllByText("Not granted", { selector: "dd" })).toHaveLength(2);
    expect(screen.getByText("Complete")).toBeVisible();
    expect(screen.getByText("Planning required; not visually verified")).toBeVisible();
    expect(screen.queryByLabelText(/run id/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/source span/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /render video/i })).toBeDisabled();
  });

  it("locks the workspace when StoryPlan acceptance is absent", async () => {
    const review = await acceptedReview();
    const access: ScenePlanAccessV1 = {
      ...accessFromReview(review),
      editable: false,
      blocker_codes: ["STORYPLAN_NOT_ACCEPTED"],
      collection: null,
    };
    renderWorkspace(new SceneRepository(review, access));
    expect(
      await screen.findByRole("heading", { name: "Scene planning locked" }),
    ).toBeVisible();
    expect(screen.getByText("STORYPLAN_NOT_ACCEPTED")).toBeVisible();
  });

  it("tracks meaningful dirty state, confirms navigation, resets, and saves explicitly", async () => {
    const repository = new SceneRepository(await acceptedReview());
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    renderWorkspace(repository);
    const objective = await screen.findByLabelText("Scene objective");
    await userEvent.type(objective, " revised");
    expect(screen.getByText(/Unsaved scene changes/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Scene 2:/ }));
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: /Scene 1:/ })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Reset unsaved changes" }));
    expect(screen.getByText(/Unsaved changes reset/)).toBeVisible();

    await userEvent.type(screen.getByLabelText("Environment"), "Quiet kitchen");
    await userEvent.click(screen.getByRole("button", { name: "Save revision" }));
    expect(repository.saveSceneRevision).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Scene revision saved/)).toBeVisible();
    expect(screen.getByLabelText("Environment")).toHaveValue("Quiet kitchen");
    await userEvent.click(
      await screen.findByRole("button", { name: "Restore as new revision" }),
    );
    expect(repository.restoreSceneRevision).toHaveBeenCalledWith(
      runId,
      "scene_01",
      expect.objectContaining({
        restore_scene_revision_id: "scene-revision-0001-01",
      }),
    );
  });

  it("preserves edits after typed save failure", async () => {
    const repository = new SceneRepository(await acceptedReview());
    repository.saveSceneRevision.mockResolvedValueOnce({
      schema_version: 1,
      access: null,
      error: {
        schema_version: 1,
        request_id: "request-stale",
        status: "BLOCKED",
        code: "STALE_SCENE_REVISION",
        message: "The scene changed.",
        retryable: false,
        field_errors: [],
        details: {},
      },
    });
    renderWorkspace(repository);
    const environment = await screen.findByLabelText("Environment");
    await userEvent.type(environment, "Preserved edit");
    await userEvent.click(screen.getByRole("button", { name: "Save revision" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "STALE_SCENE_REVISION",
    );
    expect(environment).toHaveValue("Preserved edit");
  });

  it("submits reorder, split, merge, duplicate, acceptance, and revision requests", async () => {
    const repository = new SceneRepository(await acceptedReview());
    renderWorkspace(repository);
    await screen.findByRole("heading", { name: "Scene planning workspace" });
    await userEvent.click(screen.getByRole("button", { name: "Move down" }));
    expect(repository.reorderScene).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Merge with next" }));
    expect(repository.mergeScenes).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Duplicate as draft" }));
    expect(repository.duplicateScene).toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "Accept for visual planning" }),
    );
    expect(repository.acceptScene).toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Split scene" }));
    const splitDialog = screen.getByRole("dialog");
    await userEvent.type(within(splitDialog).getByLabelText("Split source position"), "2");
    await userEvent.type(within(splitDialog).getByLabelText("First duration"), "2");
    await userEvent.type(within(splitDialog).getByLabelText("Second duration"), "3");
    await userEvent.click(
      within(splitDialog).getByRole("button", { name: "Create split revision" }),
    );
    expect(repository.splitScene).toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Request revision" }));
    const revisionDialog = screen.getByRole("dialog");
    await userEvent.selectOptions(
      within(revisionDialog).getByLabelText("Reason"),
      "CONTINUITY_NEEDS_REVISION",
    );
    await userEvent.type(
      within(revisionDialog).getByLabelText("Optional note"),
      "Clarify continuity.",
    );
    await userEvent.click(
      within(revisionDialog).getByRole("button", {
        name: "Submit revision request",
      }),
    );
    expect(repository.requestSceneRevision).toHaveBeenCalled();
  });

  it("provides keyboard semantics, responsive containment, and no generation controls", async () => {
    renderWorkspace(new SceneRepository(await acceptedReview()));
    expect(await screen.findByRole("navigation", { name: "Scene planning navigation" })).toBeVisible();
    expect(document.querySelectorAll("[tabindex]:not([tabindex='0']):not([tabindex='-1'])")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /generate image/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /choose provider/i })).not.toBeInTheDocument();
    expect(globalCss).toContain("grid-template-columns: minmax(14rem");
    expect(globalCss).toContain("@media (max-width: 700px)");
    expect(globalCss).toContain("grid-template-columns: 1fr");
    expect(globalCss).toContain(".mobile-scene-detail .scene-navigation");
    expect(globalCss).toContain("overflow-wrap: anywhere");
  });
});
