import type {
  NarrationAudioArtifactV1,
  NarrationStageAccessV1,
  NarrationStageApprovalV1,
  NarrationStageOperationResultV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

const sha = /^[0-9a-f]{64}$/;
const id = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const lockKeys = [
  "full_render_enabled",
  "timeline_execution_authority",
  "renderer_execution_authority",
  "render_authority",
  "video_render_authority",
  "subtitle_generation_capability",
  "media_muxing_capability",
  "output_creation_capability",
  "execution_job_creation_capability",
  "final_media_capability",
] as const;

function fail(): never {
  throw new ProductionContractError();
}
function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) fail();
  return value as Record<string, unknown>;
}
function text(value: unknown, pattern?: RegExp): string {
  if (typeof value !== "string" || (pattern && !pattern.test(value))) fail();
  return value;
}
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) fail();
  return value;
}
function bool(value: unknown): boolean {
  if (typeof value !== "boolean") fail();
  return value;
}
function locks(item: Record<string, unknown>): void {
  if (lockKeys.some((key) => item[key] !== false)) fail();
}
function safeProjection(item: Record<string, unknown>): void {
  const serialized = JSON.stringify(item).toLowerCase();
  for (const forbidden of [
    "api_key", "authorization", "credential", "output_path",
    "artifact_path", "filesystem", "command",
  ]) {
    if (serialized.includes(forbidden)) fail();
  }
}
function detached<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function validateNarrationApproval(value: unknown): NarrationStageApprovalV1 {
  const item = record(value);
  if (
    item.schema_version !== 1 ||
    item.purpose !== "NARRATION_TTS_ARTIFACT_GENERATION" ||
    item.process_local !== true
  ) fail();
  locks(item);
  text(item.approval_id, id);
  text(item.approval_revision_id, id);
  text(item.run_id, id);
  text(item.execution_package_id, id);
  text(item.execution_package_revision_id, id);
  text(item.package_source_authority_sha256, sha);
  text(item.narration_source_sha256, sha);
  text(item.narration_stage_source_authority_sha256, sha);
  integer(item.effective_timeline_duration_ms, 1);
  bool(item.current);
  safeProjection(item);
  return detached(item) as unknown as NarrationStageApprovalV1;
}

export function validateNarrationArtifact(value: unknown): NarrationAudioArtifactV1 {
  const item = record(value);
  if (
    item.schema_version !== 1 ||
    item.mime_type !== "audio/wav" ||
    item.extension !== "wav" ||
    item.container !== "wav" ||
    item.codec !== "pcm" ||
    item.process_local !== true
  ) fail();
  locks(item);
  text(item.artifact_id, id);
  text(item.artifact_revision_id, id);
  text(item.run_id, id);
  text(item.audio_sha256, sha);
  text(item.tts_request_sha256, sha);
  integer(item.byte_length, 1);
  integer(item.measured_duration_ms, 1);
  const comparison = record(item.duration_comparison);
  if (comparison.schema_version !== 1) fail();
  integer(comparison.measured_audio_duration_ms, 1);
  integer(comparison.accepted_timeline_duration_ms, 1);
  if (
    typeof comparison.duration_delta_ratio !== "number" ||
    !Number.isFinite(comparison.duration_delta_ratio)
  ) fail();
  const accepted =
    item.status === "ACCEPTED_FOR_RENDERER_STAGE_REVIEW" && item.current === true;
  if (
    bool(item.narration_audio_artifact_accepted) !== accepted ||
    bool(item.eligible_for_renderer_stage_review) !== accepted
  ) fail();
  safeProjection(item);
  return detached(item) as unknown as NarrationAudioArtifactV1;
}

export function validateNarrationStageAccess(value: unknown): NarrationStageAccessV1 {
  const item = record(value);
  if (item.schema_version !== 1 || item.process_local !== true) fail();
  locks(item);
  text(item.run_id, id);
  if (!Array.isArray(item.blocker_codes)) fail();
  for (const key of [
    "narration_stage_review_capability",
    "narration_stage_approval_capability",
    "narration_generation_capability",
    "tts_capability",
    "audio_generation_capability",
    "audio_measurement_capability",
    "audio_artifact_creation_capability",
    "audio_artifact_review_capability",
    "eligible_for_renderer_stage_review",
    "generation_in_progress",
  ]) bool(item[key]);
  const provider = record(item.provider_configuration);
  if (
    provider.schema_version !== 1 ||
    provider.provider_id !== "gemini" ||
    provider.model_id !== "gemini-3.1-flash-tts-preview" ||
    provider.voice_id !== "Callirrhoe" ||
    provider.language !== "vi-VN" ||
    provider.audio_format !== "audio/wav"
  ) fail();
  bool(provider.provider_configured);
  if (item.narration_source !== null) {
    const source = record(item.narration_source);
    if (source.schema_version !== 1 || source.editable !== false) fail();
    text(source.narration_text);
    text(source.narration_source_sha256, sha);
    integer(source.character_count, 1);
    integer(source.utf8_byte_count, 1);
  }
  if (item.approval !== null) validateNarrationApproval(item.approval);
  if (item.artifact !== null) validateNarrationArtifact(item.artifact);
  const generation =
    provider.provider_configured === true &&
    item.approval !== null &&
    record(item.approval).current === true;
  for (const key of [
    "narration_generation_capability",
    "tts_capability",
    "audio_generation_capability",
    "audio_measurement_capability",
    "audio_artifact_creation_capability",
  ]) {
    if (item[key] !== generation) fail();
  }
  const review =
    item.artifact !== null &&
    record(item.artifact).current === true &&
    record(item.artifact).status === "GENERATED_FOR_REVIEW";
  if (item.audio_artifact_review_capability !== review) fail();
  safeProjection(item);
  return detached(item) as unknown as NarrationStageAccessV1;
}

export function validateNarrationStageOperation(
  value: unknown,
): NarrationStageOperationResultV1 {
  const item = record(value);
  if (item.schema_version !== 1) fail();
  const result = {
    schema_version: 1 as const,
    access:
      item.access === null ? null : validateNarrationStageAccess(item.access),
    approval:
      item.approval === null ? null : validateNarrationApproval(item.approval),
    attempt: item.attempt === null ? null : detached(record(item.attempt)),
    artifact:
      item.artifact === null ? null : validateNarrationArtifact(item.artifact),
    error: item.error === null ? null : detached(record(item.error)),
  } as NarrationStageOperationResultV1;
  if (
    result.error !== null &&
    (result.access !== null || result.approval !== null || result.artifact !== null)
  ) fail();
  safeProjection(item);
  return result;
}
