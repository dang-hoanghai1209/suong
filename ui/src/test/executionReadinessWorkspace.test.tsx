import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import type { ProductionRepository } from "../api/productionClient";
import { validateExecutionReadinessAccess } from "../api/executionReadinessValidation";
import { App } from "../app/App";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  ExecutionReadinessAccessV1,
  ExecutionReadinessReportV1,
  ReadinessCapabilityLocksV1,
} from "../contracts/v1/production";
import { MockProductionRepository } from "../mock/mockProductionRepository";

const runId = "plan-0123456789abcdef0123";
const limitations = [
  "PROCESS_LOCAL_AUTHORITY",
  "HOST_RESTART_RESETS_REVIEW_STATE",
  "REAL_PROVIDER_RUNTIME_NOT_EVALUATED",
  "TTS_NOT_GENERATED",
  "AUDIO_DURATION_NOT_MEASURED",
  "AUDIO_ALIGNMENT_NOT_VERIFIED",
  "RENDERER_NOT_EXECUTED",
  "FFMPEG_NOT_PROBED",
  "FRAME_OUTPUT_NOT_VERIFIED",
  "VIDEO_OUTPUT_NOT_VERIFIED",
  "COLOR_ACCURACY_NOT_VERIFIED",
  "MOTION_SMOOTHNESS_NOT_VERIFIED",
  "TRANSITION_RENDER_NOT_VERIFIED",
  "FINAL_MEDIA_NOT_CREATED",
  "OUTPUT_STORAGE_NOT_EVALUATED",
] as const;
const locks: ReadinessCapabilityLocksV1 = {
  full_render_enabled: false,
  render_authority: false,
  renderer_execution_authority: false,
  video_render_authority: false,
  timeline_execution_authority: false,
  narration_generation_capability: false,
  tts_capability: false,
  audio_generation_capability: false,
  subtitle_generation_capability: false,
  media_muxing_capability: false,
  final_media_capability: false,
  output_creation_capability: false,
  execution_job_creation_capability: false,
};

function report(
  overrides: Partial<ExecutionReadinessReportV1> = {},
): ExecutionReadinessReportV1 {
  return {
    ...locks,
    schema_version: 1,
    report_id: "readiness-report-0001",
    report_revision_id: "readiness-report-revision-0001",
    report_revision_number: 1,
    run_id: runId,
    source_authority_sha256: "a".repeat(64),
    status: "READY_FOR_EXECUTION_ENABLEMENT_REVIEW",
    current: true,
    planning_package_ready: true,
    future_execution_enablement_review_eligible: true,
    approved_for_separate_execution_enablement_review: false,
    blocker_count: 0,
    warning_count: 1,
    informational_count: 1,
    stage_checks: [
      "RUN",
      "STORY_PLAN",
      "SCENE_PLANNING",
      "VISUAL_CANDIDATES",
      "COMPOSITION_PLANNING",
      "TIMELINE_PLANNING",
      "NARRATION_SOURCE",
      "ARTIFACT_INTEGRITY",
      "AUTHORITY_FRESHNESS",
      "EXECUTION_CAPABILITY_LOCKS",
      "PROCESS_LOCAL_LIMITATIONS",
    ].map((stage, index) => ({
      schema_version: 1,
      check_id: `readiness-stage-check-${String(index + 1).padStart(4, "0")}`,
      stage,
      status: stage === "PROCESS_LOCAL_LIMITATIONS" ? "WARNING" : "PASS",
      blocker_codes: [],
      warning_codes:
        stage === "PROCESS_LOCAL_LIMITATIONS"
          ? ["PROCESS_LOCAL_AUTHORITY"]
          : [],
      informational_codes:
        stage === "PROCESS_LOCAL_LIMITATIONS"
          ? ["RUNTIME_EXECUTION_ENVIRONMENT_NOT_EVALUATED"]
          : [],
      bound_revision_ids: [`revision-${index + 1}`],
      summary: `${stage} planning check.`,
      required_for_review_eligibility:
        stage !== "PROCESS_LOCAL_LIMITATIONS",
      checked_at: "2026-01-01T00:00:00Z",
    })),
    scene_checks: [
      {
        schema_version: 1,
        scene_check_id: "readiness-scene-check-0001",
        position: 1,
        scene_id: "scene_01",
        scene_revision_id: "scene-revision-0001-01",
        semantic_beat_id: "beat_01",
        source_coverage: { schema_version: 1, start: 0, end: 16 },
        visual_collection_revision_id: "visual-collection-revision-0001",
        accepted_candidate_id: "visual-candidate-0001-01",
        candidate_revision_id: "visual-candidate-revision-0001",
        artifact_sha256: "b".repeat(64),
        artifact_mime: "image/png",
        artifact_width: 576,
        artifact_height: 1024,
        artifact_registered: true,
        artifact_technically_valid: true,
        composition_id: "scene-composition-0001",
        composition_revision_id: "composition-revision-0001",
        composition_accepted: true,
        timeline_segment_id: "timeline-segment-0001",
        timeline_segment_revision_id: "timeline-segment-revision-0001",
        start_ms: 0,
        end_ms: 4_000,
        duration_ms: 4_000,
        transition_duration_ms: 0,
        narration_alignment_status: "ALIGNED",
        blocker_codes: [],
        warning_codes: [],
        readiness_status: "PASS",
      },
    ],
    limitations: limitations.map((limitation_code) => ({
      schema_version: 1,
      limitation_code,
      description: `${limitation_code} remains an explicit limitation.`,
      acknowledgement_required: true,
      acknowledged: false,
      acknowledgement_id: null,
    })),
    acknowledgements: [],
    approval: null,
    review_history: [],
    authority: {
      schema_version: 1,
      story_plan_revision_id: "revision-0001",
      accepted_story_plan_revision_id: "revision-0001",
      narration_source_sha256: "c".repeat(64),
      scene_collection_revision_id: "scene-plan-collection-revision-0001",
      visual_collection_revision_ids: ["visual-collection-revision-0001"],
      accepted_candidate_ids: ["visual-candidate-0001-01"],
      accepted_candidate_revision_ids: ["visual-candidate-revision-0001"],
      accepted_candidate_artifact_sha256s: ["b".repeat(64)],
      composition_collection_revision_id:
        "composition-collection-revision-0001",
      composition_ids: ["scene-composition-0001"],
      composition_revision_ids: ["composition-revision-0001"],
      timeline_collection_revision_id: "timeline-collection-revision-0001",
      accepted_timeline_revision_id: "timeline-collection-revision-0001",
      timeline_source_authority_sha256: "d".repeat(64),
    },
    total_effective_timeline_duration_ms: 4_000,
    narration_coverage_valid: true,
    transition_valid: true,
    target_duration_valid: true,
    process_local: true,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function access(
  current: ExecutionReadinessReportV1 | null,
): ExecutionReadinessAccessV1 {
  return {
    ...locks,
    schema_version: 1,
    run_id: runId,
    review_available: true,
    initialization_authorized: current === null,
    mutation_authorized: current !== null && current.current,
    blocker_codes: [],
    report: current,
    process_local: true,
  };
}

function repositoryWith(
  overrides: Partial<ProductionRepository>,
): ProductionRepository {
  return Object.assign(new MockProductionRepository(), overrides);
}

function renderWorkspace(repository: ProductionRepository) {
  return render(
    <MemoryRouter
      initialEntries={[`/production/runs/${runId}/execution-readiness`]}
    >
      <ProductionRepositoryProvider repository={repository}>
        <App />
      </ProductionRepositoryProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("execution-readiness workspace", () => {
  it("initializes explicitly from accepted timeline authority", async () => {
    const initialized = report();
    const initialize = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: null,
      report: initialized,
      error: null,
    });
    renderWorkspace(
      repositoryWith({
        getExecutionReadinessAccess: vi.fn().mockResolvedValue(access(null)),
        getExecutionReadinessHistory: vi.fn().mockResolvedValue(null),
        getTimelineAccess: vi.fn().mockResolvedValue({
          collection: {
            collection_revision_id: "timeline-collection-revision-0001",
            accepted_timeline_revision_id:
              "timeline-collection-revision-0001",
          },
        }),
        initializeExecutionReadiness: initialize,
      }),
    );
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Generate immutable planning-readiness report",
      }),
    );
    expect(initialize).toHaveBeenCalledWith(
      runId,
      "timeline-collection-revision-0001",
      "timeline-collection-revision-0001",
    );
  });

  it("shows planning scope, authority, checks, limitations and capability locks", async () => {
    renderWorkspace(
      repositoryWith({
        getExecutionReadinessAccess: vi.fn().mockResolvedValue(access(report())),
        getExecutionReadinessHistory: vi.fn().mockResolvedValue({
          schema_version: 1,
          run_id: runId,
          reports: [report()],
        }),
      }),
    );
    expect(
      await screen.findByText(/Planning-package readiness only/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Runtime execution environment not evaluated/)).toBeInTheDocument();
    expect(screen.getByText("No execution authority granted.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("STORY_PLAN · PASS")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /scene_01 · PASS/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("TTS_NOT_GENERATED")).toBeInTheDocument();
    expect(screen.getByText("Execution job creation: not granted")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Execute$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Render$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
  });

  it("requires and submits every unacknowledged limitation", async () => {
    const acknowledge = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: null,
      report: report(),
      error: null,
    });
    renderWorkspace(
      repositoryWith({
        getExecutionReadinessAccess: vi.fn().mockResolvedValue(access(report())),
        getExecutionReadinessHistory: vi.fn().mockResolvedValue(null),
        acknowledgeReadinessLimitations: acknowledge,
      }),
    );
    const button = await screen.findByRole("button", {
      name: "Acknowledge selected required limitations",
    });
    expect(button).toBeDisabled();
    for (const checkbox of screen.getAllByRole("checkbox")) {
      await userEvent.click(checkbox);
    }
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(acknowledge).toHaveBeenCalledWith(
      runId,
      expect.objectContaining({
        limitation_codes: [...limitations],
        acknowledged: true,
      }),
    );
  });

  it("uses an explicit planning-only approval dialog", async () => {
    const acknowledged = report({
      limitations: limitations.map((limitation_code, index) => ({
        schema_version: 1,
        limitation_code,
        description: `${limitation_code} remains an explicit limitation.`,
        acknowledgement_required: true,
        acknowledged: true,
        acknowledgement_id: `readiness-acknowledgement-${String(index + 1).padStart(4, "0")}`,
      })),
    });
    const approve = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: null,
      report: acknowledged,
      error: null,
    });
    renderWorkspace(
      repositoryWith({
        getExecutionReadinessAccess: vi.fn().mockResolvedValue(access(acknowledged)),
        getExecutionReadinessHistory: vi.fn().mockResolvedValue(null),
        approveExecutionReadiness: approve,
      }),
    );
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Review separate-task approval",
      }),
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/separate future execution-enablement implementation task/i);
    expect(dialog).toHaveTextContent(/enables no TTS, renderer, FFmpeg, media execution/i);
    const submit = screen.getByRole("button", {
      name: "Approve planning package for separate execution-enablement review",
    });
    expect(submit).toBeDisabled();
    await userEvent.click(
      screen.getByLabelText(/explicitly confirm this planning-package-only/i),
    );
    await userEvent.click(submit);
    expect(approve).toHaveBeenCalledWith(
      runId,
      expect.objectContaining({
        purpose: "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
        explicit_confirmation: true,
      }),
    );
  });

  it("supports bounded revision, rejection, and approval-clear actions", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const review = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: null,
      report: report(),
      error: null,
    });
    const clear = vi.fn().mockResolvedValue({
      schema_version: 1,
      access: null,
      report: report(),
      error: null,
    });
    const approved = report({
      status: "REVIEW_APPROVED_FOR_SEPARATE_TASK",
      approved_for_separate_execution_enablement_review: true,
      approval: {
        ...locks,
        schema_version: 1,
        approval_id: "readiness-approval-0001",
        report_revision_id: "readiness-report-revision-0001",
        source_authority_sha256: "a".repeat(64),
        purpose: "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
        approved_for_separate_execution_enablement_review: true,
        reviewer_note: null,
        created_at: "2026-01-01T00:00:00Z",
      },
    });
    renderWorkspace(
      repositoryWith({
        getExecutionReadinessAccess: vi.fn().mockResolvedValue(access(approved)),
        getExecutionReadinessHistory: vi.fn().mockResolvedValue(null),
        reviewExecutionReadiness: review,
        clearExecutionReadinessApproval: clear,
      }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Request upstream revision" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Reject planning package" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Clear current review approval" }),
    );
    expect(review).toHaveBeenCalledTimes(2);
    expect(clear).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["capability true", (value: ExecutionReadinessAccessV1) => {
      (value as { execution_job_creation_capability: boolean }).execution_job_creation_capability =
        true;
    }],
    ["scene order", (value: ExecutionReadinessAccessV1) => {
      (value.report!.scene_checks[0] as { position: number }).position = 2;
    }],
    ["timing arithmetic", (value: ExecutionReadinessAccessV1) => {
      (value.report!.scene_checks[0] as { end_ms: number }).end_ms = 3_999;
    }],
    ["fingerprint", (value: ExecutionReadinessAccessV1) => {
      (value.report as { source_authority_sha256: string }).source_authority_sha256 =
        "forged";
    }],
  ])("rejects malformed %s responses", (_label, mutate) => {
    const payload = structuredClone(access(report()));
    mutate(payload);
    expect(() => validateExecutionReadinessAccess(payload)).toThrow();
  });
});
