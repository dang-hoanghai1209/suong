import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProductionRepository } from "../api/productionClient";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  RenderArtifactV1,
  RendererStageAccessV1,
  RendererStageOperationResultV1,
} from "../contracts/v1/production";
import { MockProductionRepository } from "../mock/mockProductionRepository";
import { RendererStagePage } from "../pages/RendererStagePage";
import globalCss from "../styles/global.css?inline";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const locks = {
  full_render_enabled: false,
  final_media_capability: false,
  release_authority: false,
  publication_authority: false,
  output_creation_capability: false,
  subtitle_generation_capability: false,
} as const;

const configuration = {
  schema_version: 1,
  renderer_configured: true,
  renderer_implementation_id: "tella.render.pipeline.render",
  renderer_implementation_version: "1",
  renderer_bridge_contract_version: "accepted_candidate_renderer_bridge_v1",
  ffmpeg_available: true,
  ffmpeg_capability_version: "ffmpeg_argument_list_local_v1",
  ffprobe_available: true,
  ffprobe_capability_version: "ffprobe_mp4_stream_validation_v1",
  local_only: true,
  shell_allowed: false,
} as const;

const profile = {
  schema_version: 1,
  profile_id: "vertical_emotional_mp4",
  profile_version: "1",
  container: "mp4",
  mime_type: "video/mp4",
  width: 1080,
  height: 1920,
  aspect_ratio: "9:16",
  frame_rate: 30,
  video_codec: "h264",
  audio_codec: "aac",
  pixel_format: "yuv420p",
  validation_policy_version: "basic_mp4_validation_v1",
} as const;

const authority = {
  schema_version: 1,
  run_id: "plan-1234567890abcdef1234",
  execution_package_id: "execution-package-0001",
  execution_package_revision_id: "execution-package-revision-0001",
  package_source_authority_sha256: "a".repeat(64),
  story_plan_sha256: "b".repeat(64),
  narration_source_sha256: "c".repeat(64),
  narration_artifact_id: "narration-audio-artifact-0001",
  narration_artifact_revision_id: "narration-audio-revision-0001",
  narration_audio_sha256: "d".repeat(64),
  measured_narration_duration_ms: 35000,
  timeline_collection_revision_id: "timeline-collection-revision-0001",
  accepted_timeline_revision_id: "timeline-collection-revision-0001",
  timeline_source_authority_sha256: "e".repeat(64),
  scene_ids: ["scene_01"],
  scene_revision_ids: ["scene-revision-0001"],
  visual_candidate_ids: ["visual-candidate-0001-01"],
  visual_candidate_revision_ids: ["visual-candidate-revision-0001"],
  visual_artifact_sha256s: ["f".repeat(64)],
  composition_ids: ["scene-composition-0001"],
  composition_revision_ids: ["composition-revision-0001"],
} as const;

function artifact(): RenderArtifactV1 {
  return {
    schema_version: 1,
    ...locks,
    artifact_id: "render-artifact-0001",
    artifact_revision_id: "render-artifact-revision-0001",
    run_id: authority.run_id,
    render_package_id: "render-package-0001",
    render_package_revision_id: "render-package-revision-0001",
    render_package_sha256: "1".repeat(64),
    job_id: "render-job-0001",
    mime_type: "video/mp4",
    extension: "mp4",
    mp4_sha256: "2".repeat(64),
    byte_length: 1024,
    metadata: {
      schema_version: 1,
      duration_ms: 35000,
      width: 1080,
      height: 1920,
      video_codec: "h264",
      audio_codec: "aac",
      video_stream_count: 1,
      audio_stream_count: 1,
    },
    status: "GENERATED_FOR_REVIEW",
    current: true,
    render_artifact_accepted_for_qc: false,
    eligible_for_final_media_qc_review: false,
    superseded_reason: null,
    created_at: "2026-01-04T00:00:01Z",
    process_local: true,
  };
}

function access(overrides: Partial<RendererStageAccessV1> = {}): RendererStageAccessV1 {
  return {
    schema_version: 1,
    ...locks,
    run_id: authority.run_id,
    blocker_codes: [],
    renderer_stage_review_capability: true,
    renderer_stage_approval_capability: true,
    render_package_creation_capability: false,
    bounded_render_execution_capability: false,
    render_job_creation_capability: false,
    render_job_cancellation_capability: false,
    mp4_artifact_creation_capability: false,
    render_artifact_review_capability: false,
    eligible_for_final_media_qc_review: false,
    input_authority: authority,
    renderer_configuration: configuration,
    output_profile: profile,
    approval: null,
    render_package: null,
    job: null,
    artifact: null,
    process_local: true,
    restart_warning:
      "Host restart clears renderer-stage authority; stored MP4 bytes are not rebound.",
    ...overrides,
  };
}

function operation(
  values: Partial<RendererStageOperationResultV1> = {},
): RendererStageOperationResultV1 {
  return {
    schema_version: 1,
    access: null,
    approval: null,
    render_package: null,
    job: null,
    artifact: null,
    error: null,
    ...values,
  };
}

function renderPage(repository: ProductionRepository) {
  render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter
        initialEntries={[
          "/production/runs/plan-1234567890abcdef1234/renderer-stage",
        ]}
      >
        <Routes>
          <Route
            path="/production/runs/:runId/renderer-stage"
            element={<RendererStagePage />}
          />
        </Routes>
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

describe("bounded renderer-stage workspace", () => {
  it("shows exact authority, fixed profile and final-media locks", async () => {
    const repository = Object.assign(new MockProductionRepository(), {
      getRendererStageAccess: vi.fn().mockResolvedValue(access()),
    });
    renderPage(repository);
    expect(await screen.findByText("Sealed input authority")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Renderer stage" })).toBeVisible();
    expect(screen.getByText("1080×1920")).toBeVisible();
    expect(screen.getByText(authority.narration_audio_sha256)).toBeVisible();
    expect(screen.getByText("Final media: not granted")).toBeVisible();
    expect(screen.queryByLabelText(/path|filename|command|filter|codec/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /publish|export|upload/i })).not.toBeInTheDocument();
  });

  it("requires local-resource acknowledgement and confirmation", async () => {
    const approve = vi.fn().mockResolvedValue(operation());
    const repository = Object.assign(new MockProductionRepository(), {
      getRendererStageAccess: vi.fn().mockResolvedValue(access()),
      approveRendererStage: approve,
    });
    renderPage(repository);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: "Review bounded-render approval",
      }),
    );
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", {
      name: "Create renderer-stage approval",
    });
    expect(submit).toBeDisabled();
    await user.click(within(dialog).getByLabelText(/local CPU/i));
    await user.click(within(dialog).getByLabelText(/explicitly approve/i));
    await user.click(submit);
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    expect(approve).toHaveBeenCalledWith(
      authority.run_id,
      expect.objectContaining({
        narration_audio_sha256: authority.narration_audio_sha256,
        explicit_confirmation: true,
      }),
    );
  });

  it("requires separate start confirmation and uses bounded polling", async () => {
    const renderPackage = {
      schema_version: 1,
      ...locks,
      render_package_id: "render-package-0001",
      render_package_revision_id: "render-package-revision-0001",
      render_package_revision: 1,
      approval_id: "renderer-stage-approval-0001",
      approval_revision_id: "renderer-stage-approval-revision-0001",
      input_authority: authority,
      renderer_configuration: configuration,
      output_profile: profile,
      render_package_sha256: "1".repeat(64),
      current: true,
      created_at: "2026-01-04T00:00:01Z",
      process_local: true,
    } as const;
    const start = vi.fn().mockResolvedValue(operation());
    const repository = Object.assign(new MockProductionRepository(), {
      getRendererStageAccess: vi.fn().mockResolvedValue(
        access({
          render_package: renderPackage,
          bounded_render_execution_capability: true,
          render_job_creation_capability: true,
          mp4_artifact_creation_capability: true,
        }),
      ),
      startRenderJob: start,
    });
    renderPage(repository);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Start one bounded render" }),
    );
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "Start render" });
    expect(submit).toBeDisabled();
    await user.click(within(dialog).getByLabelText(/explicitly confirm/i));
    await user.click(submit);
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
  });

  it("polls an active job at the bounded 1500 ms interval", async () => {
    const interval = vi.spyOn(window, "setInterval");
    const repository = Object.assign(new MockProductionRepository(), {
      getRendererStageAccess: vi.fn().mockResolvedValue(
        access({
          job: {
            schema_version: 1,
            ...locks,
            job_id: "render-job-0001",
            job_attempt_id: "render-job-attempt-0001",
            run_id: authority.run_id,
            render_package_id: "render-package-0001",
            render_package_revision_id: "render-package-revision-0001",
            render_package_sha256: "1".repeat(64),
            status: "RUNNING",
            progress: {
              schema_version: 1,
              completed_units: 0,
              total_units: 1,
              percent: 0,
            },
            failure_code: null,
            cancellation_reason: null,
            artifact_id: null,
            created_at: "2026-01-04T00:00:01Z",
            updated_at: "2026-01-04T00:00:02Z",
            current: true,
            process_local: true,
          },
          render_job_cancellation_capability: true,
        }),
      ),
    });
    renderPage(repository);
    expect(await screen.findByText("RUNNING")).toBeVisible();
    expect(interval).toHaveBeenCalledWith(expect.any(Function), 1500);
  });

  it("previews MP4 without autoplay and accepts only for separate QC", async () => {
    const review = vi.fn().mockResolvedValue(operation({ artifact: artifact() }));
    const repository = Object.assign(new MockProductionRepository(), {
      getRendererStageAccess: vi.fn().mockResolvedValue(
        access({
          artifact: artifact(),
          render_artifact_review_capability: true,
        }),
      ),
      reviewRenderArtifact: review,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage(repository);
    await screen.findByText("MP4 SHA-256");
    const video = document.querySelector("video");
    expect(video).not.toHaveAttribute("autoplay");
    expect(video?.getAttribute("src")).toMatch(/^\/api\/v1\/plan-only\//);
    await userEvent.click(
      screen.getByRole("button", {
        name: "Submit for separate final-media QC review",
      }),
    );
    await waitFor(() =>
      expect(review).toHaveBeenCalledWith(
        authority.run_id,
        "render-artifact-0001",
        "accept-for-qc",
        expect.objectContaining({ explicit_confirmation: true }),
      ),
    );
  });

  it("fails closed for unavailable and malformed backend responses", async () => {
    const repository = Object.assign(new MockProductionRepository(), {
      getRendererStageAccess: vi.fn().mockRejectedValue(new Error("internal path")),
    });
    renderPage(repository);
    expect(
      await screen.findByRole("heading", { name: "Renderer stage unavailable" }),
    ).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "without a safe response",
    );
  });

  it("preserves keyboard semantics and responsive containment", async () => {
    const repository = Object.assign(new MockProductionRepository(), {
      getRendererStageAccess: vi.fn().mockResolvedValue(access()),
    });
    renderPage(repository);
    expect(await screen.findByText("Sealed input authority")).toBeVisible();
    expect(
      document.querySelectorAll(
        "[tabindex]:not([tabindex='0']):not([tabindex='-1'])",
      ),
    ).toHaveLength(0);
    expect(globalCss).toContain(".readiness-layout");
    expect(globalCss).toContain("@media (max-width: 720px)");
    expect(globalCss).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(globalCss).toContain("overflow-wrap: anywhere");
  });
});
