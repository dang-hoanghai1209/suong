import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { App } from "../app/App";
import {
  ProductionBackendUnavailableError,
  ProductionContractError,
} from "../api/productionClient";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  GenerateVisualCandidatesRequestV1,
  VisualCandidateAccessV1,
  VisualCandidateMutationRequestV1,
  VisualCandidateOperationResultV1,
  VisualCandidateV1,
} from "../contracts/v1/production";
import { MockProductionRepository } from "../mock/mockProductionRepository";
import globalCss from "../styles/global.css?inline";

const runId = "visual-run-01";
const sceneId = "scene_01";
const shaA = "a".repeat(64);
const shaB = "b".repeat(64);
const createdAt = "2026-01-01T00:00:01Z";

function candidate(
  index: 1 | 2,
  status: VisualCandidateV1["status"] = "REVIEW_PENDING",
): VisualCandidateV1 {
  const id = `visual-candidate-0001-0${index}`;
  return {
    schema_version: 1,
    candidate_id: id,
    candidate_revision_id: `visual-candidate-revision-000${index}`,
    candidate_revision_number: 1,
    request_id: "visual-request-0001",
    attempt_id: "visual-attempt-0001",
    run_id: runId,
    story_revision_id: "revision-0001",
    scene_plan_collection_revision_id: "scene-plan-revision-0002",
    scene_id: sceneId,
    scene_revision_id: "scene-revision-0002-01",
    semantic_beat_id: "beat_01",
    source_coverage: { start: 0, end: 8 },
    status,
    artifact_url:
      `/api/v1/plan-only/runs/${runId}/scene-plan/scenes/${sceneId}` +
      `/visual-candidates/${id}/artifact`,
    provider_capability_label: "approved-test-provider",
    model_label: "approved-still-image-model",
    mime_type: "image/png",
    extension: ".png",
    width: 576,
    height: 1024,
    sha256: index === 1 ? shaA : shaB,
    logical_request_hash: shaA,
    provider_request_hash: index === 1 ? shaA : shaB,
    technical_validation: {
      passed: true,
      mime_valid: true,
      dimensions_valid: true,
      non_empty: true,
      animation_free: true,
      duplicate_free: true,
      blocker_codes: [],
    },
    visual_qc: {
      blocker_codes: [],
      warning_codes: index === 1 ? ["HUMAN_REVIEW_PENDING"] : [],
      information_codes: ["HUMAN_VISUAL_REVIEW_REQUIRED", "UNKNOWN_SAFE_CODE"],
    },
    review_history: [],
    accepted_for_composition_planning:
      status === "ACCEPTED_FOR_COMPOSITION_PLANNING",
    superseded_reason: status === "SUPERSEDED" ? "NEWER_CANDIDATE_ACCEPTED" : null,
    created_at: createdAt,
  };
}

function access(
  candidates: readonly VisualCandidateV1[] = [],
): VisualCandidateAccessV1 {
  return {
    schema_version: 1,
    run_id: runId,
    scene_id: sceneId,
    request_authorized: true,
    review_authorized: true,
    blocker_codes: [],
    current_story_revision_id: "revision-0001",
    accepted_story_revision_id: "revision-0001",
    current_scene_plan_collection_revision_id: "scene-plan-revision-0002",
    current_scene_revision_id: "scene-revision-0002-01",
    current_visual_collection_revision_id: "visual-collection-revision-0001",
    current_request_id: candidates.length ? "visual-request-0001" : null,
    current_accepted_candidate_id:
      candidates.find((item) => item.accepted_for_composition_planning)
        ?.candidate_id ?? null,
    provider_capability: {
      available: true,
      capability_label: "approved-test-provider",
      supports_9_16: true,
      maximum_candidate_count: 4,
      external_provider: true,
      reason_code: null,
    },
    collection: {
      schema_version: 1,
      run_id: runId,
      story_revision_id: "revision-0001",
      scene_plan_collection_revision_id: "scene-plan-revision-0002",
      scene_id: sceneId,
      scene_revision_id: "scene-revision-0002-01",
      semantic_beat_id: "beat_01",
      source_coverage: { start: 0, end: 8 },
      visual_authority_version: "visual_candidate_authority_v1",
      visual_collection_id: "visual-collection-0001",
      visual_collection_revision_id: "visual-collection-revision-0001",
      visual_collection_revision_number: 1,
      current_request_id: candidates.length ? "visual-request-0001" : null,
      current_accepted_candidate_id:
        candidates.find((item) => item.accepted_for_composition_planning)
          ?.candidate_id ?? null,
      requests: candidates.length
        ? [
            {
              request_id: "visual-request-0001",
              request_number: 1,
              visual_collection_revision_id: "visual-collection-revision-0001",
              candidate_count: candidates.length,
              aspect_ratio: "9:16",
              composition_emphasis: null,
              correction_dimensions: [],
              note: null,
              prompt_projection: "Backend-derived safe prompt projection",
              logical_request_hash: shaA,
              attempt_ids: ["visual-attempt-0001"],
              status: "SUCCESS",
              created_at: createdAt,
            },
          ]
        : [],
      attempts: candidates.length
        ? [
            {
              attempt_id: "visual-attempt-0001",
              attempt_number: 1,
              request_id: "visual-request-0001",
              candidate_ids: candidates.map((item) => item.candidate_id),
              requested_candidate_count: candidates.length,
              returned_candidate_count: candidates.length,
              invalid_candidate_count: 0,
              provider_capability_label: "approved-test-provider",
              model_label: "approved-still-image-model",
              status: "SUCCESS",
              reason_code: "VISUAL_CANDIDATES_GENERATED",
              created_at: createdAt,
            },
          ]
        : [],
      candidates,
      render_authority: false,
      video_render_authority: false,
      final_media_capability: false,
      narration_generation_capability: false,
      tts_capability: false,
      process_local: true,
      created_at: createdAt,
    },
    render_authority: false,
    video_render_authority: false,
    final_media_capability: false,
    narration_generation_capability: false,
    tts_capability: false,
    process_local: true,
  };
}

class VisualRepository extends MockProductionRepository {
  current = access();
  readonly getVisualCandidateAccess = vi.fn(async () =>
    structuredClone(this.current),
  );
  readonly generateVisualCandidates = vi.fn(
    async (
      _runId: string,
      _sceneId: string,
      _request: GenerateVisualCandidatesRequestV1,
    ): Promise<VisualCandidateOperationResultV1> => {
      this.current = access([candidate(1), candidate(2)]);
      return { schema_version: 1, access: structuredClone(this.current), error: null };
    },
  );
  readonly mutateVisualCandidate = vi.fn(
    async (
      _runId: string,
      _sceneId: string,
      candidateId: string,
      operation: "accept" | "reject" | "request-revision",
      _request: VisualCandidateMutationRequestV1,
    ): Promise<VisualCandidateOperationResultV1> => {
      const next = this.current.collection!.candidates.map((item) =>
        item.candidate_id === candidateId
          ? {
              ...item,
              status:
                operation === "accept"
                  ? ("ACCEPTED_FOR_COMPOSITION_PLANNING" as const)
                  : operation === "reject"
                    ? ("REJECTED" as const)
                    : ("REVISION_REQUESTED" as const),
              accepted_for_composition_planning: operation === "accept",
            }
          : item,
      );
      this.current = access(next);
      return { schema_version: 1, access: structuredClone(this.current), error: null };
    },
  );
}

function renderWorkspace(repository: VisualRepository) {
  return render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter
        initialEntries={[
          `/production/runs/${runId}/scenes/${sceneId}/visuals`,
        ]}
      >
        <App />
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PLAN_ONLY visual candidate workspace", () => {
  it("loads exact route authority without exposing unsafe controls", async () => {
    const repository = new VisualRepository();
    renderWorkspace(repository);
    expect(
      await screen.findByRole("heading", { name: "Visual candidate review" }),
    ).toBeVisible();
    expect(repository.getVisualCandidateAccess).toHaveBeenCalledWith(runId, sceneId);
    expect(screen.getByText(/process-local/i)).toBeVisible();
    expect(screen.queryByLabelText(/raw prompt/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/provider key/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/endpoint|url/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /tts/i })).toBeNull();
    expect(screen.getAllByText("Not granted")).toHaveLength(4);
  });

  it("validates bounded explicit generation and renders gallery, metadata, QC, and compare", async () => {
    const repository = new VisualRepository();
    renderWorkspace(repository);
    const count = await screen.findByLabelText("Candidate count");
    await userEvent.clear(count);
    await userEvent.type(count, "5");
    expect(screen.getByRole("button", { name: "Generate candidates" })).toBeDisabled();
    await userEvent.clear(count);
    await userEvent.type(count, "2");
    await userEvent.click(screen.getByRole("button", { name: "Generate candidates" }));
    expect(repository.generateVisualCandidates).toHaveBeenCalledWith(
      runId,
      sceneId,
      expect.objectContaining({ candidate_count: 2, aspect_ratio: "9:16" }),
    );
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options).toHaveLength(2);
    await userEvent.click(options[0]!);
    expect(screen.getByText("HUMAN_REVIEW_PENDING")).toBeVisible();
    expect(screen.getByText("UNKNOWN_SAFE_CODE")).toBeVisible();
    expect(screen.getByText(shaA)).toBeVisible();
    fireEvent.error(
      screen.getByAltText(`Visual candidate 1 for scene ${sceneId}`),
    );
    expect(screen.getByText("Preview unavailable")).toBeVisible();
    const compare = screen.getAllByRole("checkbox");
    await userEvent.click(compare[0]!);
    await userEvent.click(compare[1]!);
    expect(screen.getAllByAltText(/Comparison preview/)).toHaveLength(2);
  });

  it("uses explicit confirmed acceptance, rejection, and revision actions", async () => {
    const repository = new VisualRepository();
    repository.current = access([candidate(1), candidate(2)]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace(repository);
    await screen.findByRole("listbox");
    await userEvent.click(
      screen.getByRole("button", { name: "Accept for composition planning" }),
    );
    expect(repository.mutateVisualCandidate).toHaveBeenLastCalledWith(
      runId,
      sceneId,
      "visual-candidate-0001-01",
      "accept",
      expect.any(Object),
    );
    expect(
      await screen.findByText(/No render authority was granted/),
    ).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Confirm review action",
      }),
    );
    expect(repository.mutateVisualCandidate).toHaveBeenLastCalledWith(
      runId,
      sceneId,
      expect.any(String),
      "reject",
      expect.any(Object),
    );
  });

  it("shows locked, backend failure, and image failure states safely", async () => {
    const repository = new VisualRepository();
    repository.current = {
      ...access(),
      request_authorized: false,
      review_authorized: false,
      blocker_codes: ["VISUAL_PROVIDER_UNAVAILABLE"],
      provider_capability: {
        ...access().provider_capability,
        available: false,
        reason_code: "VISUAL_PROVIDER_UNAVAILABLE",
      },
      collection: null,
    };
    renderWorkspace(repository);
    expect(
      await screen.findByRole("heading", {
        name: "Visual candidate workspace locked",
      }),
    ).toBeVisible();
    expect(screen.getByText("VISUAL_PROVIDER_UNAVAILABLE")).toBeVisible();
  });

  it.each([
    [
      new ProductionContractError(),
      "The backend returned a malformed visual-candidate response.",
    ],
    [
      new ProductionBackendUnavailableError(),
      "The PLAN_ONLY visual-candidate backend is unavailable.",
    ],
  ])("fails closed when visual access loading fails", async (failure, message) => {
    const repository = new VisualRepository();
    repository.getVisualCandidateAccess.mockRejectedValueOnce(failure);
    renderWorkspace(repository);
    expect(
      await screen.findByRole("heading", {
        name: "Visual candidate workspace unavailable",
      }),
    ).toBeVisible();
    expect(screen.getByText(message)).toBeVisible();
  });

  it("has bounded responsive and keyboard-accessible presentation", async () => {
    const repository = new VisualRepository();
    repository.current = access([candidate(1), candidate(2)]);
    renderWorkspace(repository);
    expect(
      within(await screen.findByRole("listbox")).getAllByRole("option"),
    ).toHaveLength(2);
    expect(
      document.querySelectorAll("[tabindex]:not([tabindex='0']):not([tabindex='-1'])"),
    ).toHaveLength(0);
    expect(globalCss).toContain(".visual-review-layout");
    expect(globalCss).toContain("@media (max-width: 700px)");
    expect(globalCss).toContain("overflow-wrap: anywhere");
  });

  it("renders current acceptance and historical supersession without media authority", async () => {
    const repository = new VisualRepository();
    repository.current = access([
      candidate(1, "SUPERSEDED"),
      candidate(2, "ACCEPTED_FOR_COMPOSITION_PLANNING"),
    ]);
    renderWorkspace(repository);
    expect(await screen.findByText("SUPERSEDED")).toBeVisible();
    expect(
      screen.getAllByText("Accepted for composition planning")[0],
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Render video" })).toBeDisabled();
  });
});
