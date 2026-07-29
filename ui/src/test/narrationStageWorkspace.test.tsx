import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProductionRepository } from "../api/productionClient";
import { ProductionRepositoryProvider } from "../app/ProductionRepositoryContext";
import type {
  NarrationAudioArtifactV1,
  NarrationStageAccessV1,
  NarrationStageOperationResultV1,
} from "../contracts/v1/production";
import { MockProductionRepository } from "../mock/mockProductionRepository";
import { NarrationStagePage } from "../pages/NarrationStagePage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const falseLocks = {
  full_render_enabled: false,
  timeline_execution_authority: false,
  renderer_execution_authority: false,
  render_authority: false,
  video_render_authority: false,
  subtitle_generation_capability: false,
  media_muxing_capability: false,
  output_creation_capability: false,
  execution_job_creation_capability: false,
  final_media_capability: false,
} as const;

function artifact(): NarrationAudioArtifactV1 {
  return {
    schema_version: 1,
    ...falseLocks,
    artifact_id: "narration-audio-artifact-0001",
    artifact_revision_id: "narration-audio-revision-0001",
    run_id: "plan-1234567890abcdef1234",
    execution_package_id: "execution-package-0001",
    execution_package_revision_id: "execution-package-revision-0001",
    package_source_authority_sha256: "a".repeat(64),
    narration_stage_approval_id: "narration-stage-approval-0001",
    narration_stage_approval_revision_id: "narration-stage-approval-revision-0001",
    narration_source_sha256: "b".repeat(64),
    tts_request_sha256: "c".repeat(64),
    provider_id: "gemini",
    provider_implementation_version: "tella.tts.providers.GeminiTTSProvider.v1",
    model_id: "gemini-3.1-flash-tts-preview",
    voice_id: "Callirrhoe",
    language: "vi-VN",
    style_profile_id: "gentle_female_soft_slow_no_whisper",
    style_profile_version: "1",
    mime_type: "audio/wav",
    extension: "wav",
    audio_sha256: "d".repeat(64),
    byte_length: 172,
    measured_duration_ms: 35000,
    container: "wav",
    codec: "pcm",
    sample_rate_hz: null,
    channels: null,
    validation_policy_version: "narration_audio_validation_v1",
    artifact_contract_version: "narration_audio_artifact_v1",
    status: "GENERATED_FOR_REVIEW",
    current: true,
    narration_audio_artifact_accepted: false,
    eligible_for_renderer_stage_review: false,
    duration_comparison: {
      schema_version: 1,
      measured_audio_duration_ms: 35000,
      accepted_timeline_duration_ms: 35000,
      duration_delta_ms: 0,
      duration_delta_ratio: 0,
      duration_alignment_status: "WITHIN_POLICY",
      timeline_realignment_required: false,
      duration_policy_reason_code: "NARRATION_DURATION_WITHIN_TEN_PERCENT",
    },
    superseded_reason: null,
    created_at: "2026-01-03T00:00:01Z",
    process_local: true,
  };
}

function access(audio: NarrationAudioArtifactV1 | null = null): NarrationStageAccessV1 {
  return {
    schema_version: 1,
    ...falseLocks,
    run_id: "plan-1234567890abcdef1234",
    blocker_codes: [],
    narration_stage_review_capability: true,
    narration_stage_approval_capability: true,
    narration_generation_capability: true,
    tts_capability: true,
    audio_generation_capability: true,
    audio_measurement_capability: true,
    audio_artifact_creation_capability: true,
    audio_artifact_review_capability: audio !== null,
    eligible_for_renderer_stage_review: false,
    execution_package_id: "execution-package-0001",
    execution_package_revision_id: "execution-package-revision-0001",
    package_source_authority_sha256: "a".repeat(64),
    narration_source: {
      schema_version: 1,
      narration_text: "Đây là lời kể chuẩn.\n\nKhông thể chỉnh sửa tại bước này.",
      narration_source_sha256: "b".repeat(64),
      character_count: 56,
      utf8_byte_count: 70,
      editable: false,
    },
    provider_configuration: {
      schema_version: 1,
      provider_configured: true,
      provider_id: "gemini",
      provider_display_name: "Gemini TTS",
      provider_implementation_version: "tella.tts.providers.GeminiTTSProvider.v1",
      model_id: "gemini-3.1-flash-tts-preview",
      model_display_name: "Gemini 3.1 Flash TTS Preview",
      voice_id: "Callirrhoe",
      voice_display_name: "Callirrhoe",
      language: "vi-VN",
      style_profile_id: "gentle_female_soft_slow_no_whisper",
      style_profile_version: "1",
      audio_format: "audio/wav",
      audio_validation_policy_version: "narration_audio_validation_v1",
    },
    approval: audio
      ? {
          schema_version: 1,
          ...falseLocks,
          approval_id: "narration-stage-approval-0001",
          approval_revision_id: "narration-stage-approval-revision-0001",
          run_id: "plan-1234567890abcdef1234",
          execution_package_id: "execution-package-0001",
          execution_package_revision_id: "execution-package-revision-0001",
          package_source_authority_sha256: "a".repeat(64),
          narration_source_sha256: "b".repeat(64),
          accepted_timeline_revision_id: "timeline-revision-0001",
          effective_timeline_duration_ms: 35000,
          provider_id: "gemini",
          provider_implementation_version: "tella.tts.providers.GeminiTTSProvider.v1",
          model_id: "gemini-3.1-flash-tts-preview",
          voice_id: "Callirrhoe",
          language: "vi-VN",
          style_profile_id: "gentle_female_soft_slow_no_whisper",
          style_profile_version: "1",
          audio_format: "audio/wav",
          audio_validation_policy_version: "narration_audio_validation_v1",
          purpose: "NARRATION_TTS_ARTIFACT_GENERATION",
          narration_stage_source_authority_sha256: "e".repeat(64),
          current: true,
          note: null,
          created_at: "2026-01-03T00:00:01Z",
          process_local: true,
        }
      : null,
    artifact: audio,
    generation_in_progress: false,
    attempt_count: audio ? 1 : 0,
    artifact_history_count: audio ? 1 : 0,
    review_history_count: audio ? 1 : 0,
    process_local: true,
    restart_warning:
      "Host restart clears narration-stage authority; stored bytes are not rebound.",
  };
}

function result(
  values: Partial<NarrationStageOperationResultV1> = {},
): NarrationStageOperationResultV1 {
  return {
    schema_version: 1,
    access: null,
    approval: null,
    attempt: null,
    artifact: null,
    error: null,
    ...values,
  };
}

function renderPage(repository: ProductionRepository) {
  render(
    <ProductionRepositoryProvider repository={repository}>
      <MemoryRouter initialEntries={["/production/runs/plan-1234567890abcdef1234/narration-stage"]}>
        <Routes>
          <Route path="/production/runs/:runId/narration-stage" element={<NarrationStagePage />} />
        </Routes>
      </MemoryRouter>
    </ProductionRepositoryProvider>,
  );
}

describe("narration and TTS workspace", () => {
  it("shows immutable narration, provider configuration, warnings and locks", async () => {
    const repository = Object.assign(new MockProductionRepository(), {
      getNarrationStageAccess: vi.fn().mockResolvedValue(access()),
    });
    renderPage(repository);
    expect(await screen.findByText(/Đây là lời kể chuẩn/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Narration and TTS" })).toBeVisible();
    expect(screen.getByText("Callirrhoe")).toBeVisible();
    expect(screen.getByText(/Host restart clears/)).toBeVisible();
    expect(screen.queryByRole("textbox", { name: /narration/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Ready to render/i)).not.toBeInTheDocument();
    expect(screen.getByText("Renderer execution: not granted")).toBeVisible();
  });

  it("requires explicit quota acknowledgement and confirmation before approval", async () => {
    const approve = vi.fn().mockResolvedValue(result());
    const repository = Object.assign(new MockProductionRepository(), {
      getNarrationStageAccess: vi.fn().mockResolvedValue(access()),
      approveNarrationStage: approve,
    });
    renderPage(repository);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Review narration-stage approval" }));
    const submit = screen.getByRole("button", { name: "Create narration-stage approval" });
    expect(submit).toBeDisabled();
    await user.click(screen.getByLabelText(/quota or cost/i));
    await user.click(screen.getByLabelText(/explicitly confirm narration-stage/i));
    await user.click(submit);
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
  });

  it("renders audio without autoplay and accepts only for separate review", async () => {
    const review = vi.fn().mockResolvedValue(result({ artifact: artifact() }));
    const repository = Object.assign(new MockProductionRepository(), {
      getNarrationStageAccess: vi.fn().mockResolvedValue(access(artifact())),
      reviewNarrationAudio: review,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage(repository);
    const audio = await screen.findByText("Audio artifact generated for review.");
    expect(audio).toBeVisible();
    const player = document.querySelector("audio");
    expect(player).not.toHaveAttribute("autoplay");
    expect(player?.getAttribute("src")).toMatch(/^\/api\/v1\/plan-only\//);
    await userEvent.click(
      screen.getByRole("button", { name: "Accept for separate renderer-stage review" }),
    );
    await waitFor(() => expect(review).toHaveBeenCalledWith(
      "plan-1234567890abcdef1234",
      "narration-audio-artifact-0001",
      "accept",
      expect.objectContaining({ explicit_confirmation: true }),
    ));
  });

  it("requires a separate explicit generation confirmation and blocks duplicates", async () => {
    const staged = {
      ...access(artifact()),
      artifact: null,
      audio_artifact_review_capability: false,
      artifact_history_count: 0,
      review_history_count: 0,
    };
    const generate = vi.fn().mockResolvedValue(result({ artifact: artifact() }));
    const repository = Object.assign(new MockProductionRepository(), {
      getNarrationStageAccess: vi.fn().mockResolvedValue(staged),
      generateNarrationAudio: generate,
    });
    renderPage(repository);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Generate narration audio" }));
    const submit = within(screen.getByRole("dialog")).getByRole("button", {
      name: "Generate narration audio",
    });
    expect(submit).toBeDisabled();
    await user.click(screen.getByLabelText(/explicitly confirm this TTS request/i));
    await user.click(submit);
    await waitFor(() => expect(generate).toHaveBeenCalledTimes(1));
    expect(generate).toHaveBeenCalledWith(
      "plan-1234567890abcdef1234",
      expect.objectContaining({ explicit_confirmation: true }),
    );
  });

  it("fails closed when the backend is unavailable", async () => {
    const repository = Object.assign(new MockProductionRepository(), {
      getNarrationStageAccess: vi.fn().mockRejectedValue(new Error("offline")),
    });
    renderPage(repository);
    expect(await screen.findByRole("heading", { name: "Narration and TTS unavailable" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("without a safe response");
  });
});
