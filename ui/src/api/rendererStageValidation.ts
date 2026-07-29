import type {
  RenderArtifactV1,
  RenderInputAuthorityV1,
  RenderJobV1,
  RenderPackageV1,
  RendererConfigurationV1,
  RendererOutputProfileV1,
  RendererStageAccessV1,
  RendererStageApprovalV1,
  RendererStageHistoryV1,
  RendererStageOperationResultV1,
} from "../contracts/v1/production";
import { ProductionContractError } from "./productionClient";

type UnknownRecord = Record<string, unknown>;

const sha = /^[0-9a-f]{64}$/;
const forbidden = new Set([
  "api_key",
  "authorization",
  "credential",
  "path",
  "input_path",
  "output_path",
  "filename",
  "command",
  "arguments",
  "ffmpeg_arguments",
  "filter_graph",
  "provider_url",
  "url",
]);

function record(value: unknown): UnknownRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProductionContractError();
  }
  return value as UnknownRecord;
}

function exactKeys(item: UnknownRecord, keys: readonly string[]) {
  const actual = Object.keys(item).sort();
  const expected = [...keys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    throw new ProductionContractError();
  }
}

function assertNoForbidden(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertNoForbidden);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, nested] of Object.entries(value as UnknownRecord)) {
    if (forbidden.has(key.toLowerCase())) throw new ProductionContractError();
    assertNoForbidden(nested);
  }
}

function text(value: unknown): asserts value is string {
  if (typeof value !== "string" || value.length === 0) {
    throw new ProductionContractError();
  }
}

function hash(value: unknown): asserts value is string {
  if (typeof value !== "string" || !sha.test(value)) {
    throw new ProductionContractError();
  }
}

function integer(value: unknown, minimum = 0): asserts value is number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new ProductionContractError();
  }
}

function boolean(value: unknown): asserts value is boolean {
  if (typeof value !== "boolean") throw new ProductionContractError();
}

function stringArray(value: unknown): asserts value is string[] {
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
    throw new ProductionContractError();
  }
}

function locks(item: UnknownRecord) {
  for (const key of [
    "full_render_enabled",
    "final_media_capability",
    "release_authority",
    "publication_authority",
    "output_creation_capability",
    "subtitle_generation_capability",
  ]) {
    if (item[key] !== false) throw new ProductionContractError();
  }
}

function detached<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function validateRendererConfiguration(
  value: unknown,
): RendererConfigurationV1 {
  const item = record(value);
  exactKeys(item, [
    "schema_version",
    "renderer_configured",
    "renderer_implementation_id",
    "renderer_implementation_version",
    "renderer_bridge_contract_version",
    "ffmpeg_available",
    "ffmpeg_capability_version",
    "ffprobe_available",
    "ffprobe_capability_version",
    "local_only",
    "shell_allowed",
  ]);
  if (
    item.schema_version !== 1 ||
    item.renderer_implementation_id !== "tella.render.pipeline.render" ||
    item.renderer_implementation_version !== "1" ||
    item.renderer_bridge_contract_version !==
      "accepted_candidate_renderer_bridge_v1" ||
    item.ffmpeg_capability_version !== "ffmpeg_argument_list_local_v1" ||
    item.ffprobe_capability_version !== "ffprobe_mp4_stream_validation_v1" ||
    item.local_only !== true ||
    item.shell_allowed !== false
  ) {
    throw new ProductionContractError();
  }
  boolean(item.renderer_configured);
  boolean(item.ffmpeg_available);
  boolean(item.ffprobe_available);
  if (
    item.renderer_configured !==
    (item.ffmpeg_available && item.ffprobe_available)
  ) {
    throw new ProductionContractError();
  }
  return detached(item) as unknown as RendererConfigurationV1;
}

export function validateRendererOutputProfile(
  value: unknown,
): RendererOutputProfileV1 {
  const item = record(value);
  exactKeys(item, [
    "schema_version",
    "profile_id",
    "profile_version",
    "container",
    "mime_type",
    "width",
    "height",
    "aspect_ratio",
    "frame_rate",
    "video_codec",
    "audio_codec",
    "pixel_format",
    "validation_policy_version",
  ]);
  if (
    item.schema_version !== 1 ||
    item.profile_id !== "vertical_emotional_mp4" ||
    item.profile_version !== "1" ||
    item.container !== "mp4" ||
    item.mime_type !== "video/mp4" ||
    item.width !== 1080 ||
    item.height !== 1920 ||
    item.aspect_ratio !== "9:16" ||
    item.frame_rate !== 30 ||
    item.video_codec !== "h264" ||
    item.audio_codec !== "aac" ||
    item.pixel_format !== "yuv420p" ||
    item.validation_policy_version !== "basic_mp4_validation_v1"
  ) {
    throw new ProductionContractError();
  }
  return detached(item) as unknown as RendererOutputProfileV1;
}

export function validateRenderInputAuthority(
  value: unknown,
): RenderInputAuthorityV1 {
  const item = record(value);
  exactKeys(item, [
    "schema_version",
    "run_id",
    "execution_package_id",
    "execution_package_revision_id",
    "package_source_authority_sha256",
    "story_plan_sha256",
    "narration_source_sha256",
    "narration_artifact_id",
    "narration_artifact_revision_id",
    "narration_audio_sha256",
    "measured_narration_duration_ms",
    "timeline_collection_revision_id",
    "accepted_timeline_revision_id",
    "timeline_source_authority_sha256",
    "scene_ids",
    "scene_revision_ids",
    "visual_candidate_ids",
    "visual_candidate_revision_ids",
    "visual_artifact_sha256s",
    "composition_ids",
    "composition_revision_ids",
  ]);
  if (item.schema_version !== 1) throw new ProductionContractError();
  for (const key of [
    "run_id",
    "execution_package_id",
    "execution_package_revision_id",
    "narration_artifact_id",
    "narration_artifact_revision_id",
    "timeline_collection_revision_id",
    "accepted_timeline_revision_id",
  ]) {
    text(item[key]);
  }
  for (const key of [
    "package_source_authority_sha256",
    "story_plan_sha256",
    "narration_source_sha256",
    "narration_audio_sha256",
    "timeline_source_authority_sha256",
  ]) {
    hash(item[key]);
  }
  integer(item.measured_narration_duration_ms, 1);
  const arrays = [
    item.scene_ids,
    item.scene_revision_ids,
    item.visual_candidate_ids,
    item.visual_candidate_revision_ids,
    item.visual_artifact_sha256s,
    item.composition_ids,
    item.composition_revision_ids,
  ];
  arrays.forEach(stringArray);
  if (
    arrays.some(
      (entries) =>
        (entries as string[]).length !== (item.scene_ids as string[]).length,
    ) ||
    (item.scene_ids as string[]).length === 0 ||
    (item.visual_artifact_sha256s as string[]).some((entry) => !sha.test(entry))
  ) {
    throw new ProductionContractError();
  }
  return detached(item) as unknown as RenderInputAuthorityV1;
}

function validateApproval(value: unknown): RendererStageApprovalV1 {
  const item = record(value);
  locks(item);
  if (
    item.schema_version !== 1 ||
    item.purpose !== "BOUNDED_MP4_RENDER_EXECUTION" ||
    item.process_local !== true ||
    typeof item.current !== "boolean" ||
    (item.note !== null && typeof item.note !== "string")
  ) {
    throw new ProductionContractError();
  }
  text(item.approval_id);
  text(item.approval_revision_id);
  hash(item.renderer_stage_source_authority_sha256);
  text(item.created_at);
  return detached({
    ...item,
    input_authority: validateRenderInputAuthority(item.input_authority),
    renderer_configuration: validateRendererConfiguration(
      item.renderer_configuration,
    ),
    output_profile: validateRendererOutputProfile(item.output_profile),
  }) as unknown as RendererStageApprovalV1;
}

function validatePackage(value: unknown): RenderPackageV1 {
  const item = record(value);
  locks(item);
  if (
    item.schema_version !== 1 ||
    item.render_package_revision !== 1 ||
    item.process_local !== true ||
    typeof item.current !== "boolean"
  ) {
    throw new ProductionContractError();
  }
  for (const key of [
    "render_package_id",
    "render_package_revision_id",
    "approval_id",
    "approval_revision_id",
    "created_at",
  ]) {
    text(item[key]);
  }
  hash(item.render_package_sha256);
  return detached({
    ...item,
    input_authority: validateRenderInputAuthority(item.input_authority),
    renderer_configuration: validateRendererConfiguration(
      item.renderer_configuration,
    ),
    output_profile: validateRendererOutputProfile(item.output_profile),
  }) as unknown as RenderPackageV1;
}

export function validateRenderJob(value: unknown): RenderJobV1 {
  const item = record(value);
  locks(item);
  const statuses = new Set([
    "AWAITING_APPROVAL",
    "APPROVED_FOR_BOUNDED_RENDER",
    "QUEUED",
    "RUNNING",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "SUCCEEDED_FOR_REVIEW",
    "FAILED",
    "SUPERSEDED",
  ]);
  if (
    item.schema_version !== 1 ||
    !statuses.has(String(item.status)) ||
    item.process_local !== true ||
    typeof item.current !== "boolean" ||
    (item.failure_code !== null && typeof item.failure_code !== "string") ||
    (item.cancellation_reason !== null &&
      typeof item.cancellation_reason !== "string") ||
    (item.artifact_id !== null && typeof item.artifact_id !== "string")
  ) {
    throw new ProductionContractError();
  }
  for (const key of [
    "job_id",
    "job_attempt_id",
    "run_id",
    "render_package_id",
    "render_package_revision_id",
    "created_at",
    "updated_at",
  ]) {
    text(item[key]);
  }
  hash(item.render_package_sha256);
  const progress = record(item.progress);
  if (
    progress.schema_version !== 1 ||
    progress.total_units !== 1 ||
    !Number.isInteger(progress.completed_units) ||
    !Number.isInteger(progress.percent) ||
    progress.percent !== (progress.completed_units as number) * 100
  ) {
    throw new ProductionContractError();
  }
  return detached(item) as unknown as RenderJobV1;
}

export function validateRenderArtifact(value: unknown): RenderArtifactV1 {
  const item = record(value);
  locks(item);
  const statuses = new Set([
    "GENERATED_FOR_REVIEW",
    "ACCEPTED_FOR_FINAL_MEDIA_QC_REVIEW",
    "REJECTED",
    "RERENDER_REQUESTED",
    "SUPERSEDED",
  ]);
  if (
    item.schema_version !== 1 ||
    item.mime_type !== "video/mp4" ||
    item.extension !== "mp4" ||
    !statuses.has(String(item.status)) ||
    item.process_local !== true ||
    typeof item.current !== "boolean" ||
    typeof item.render_artifact_accepted_for_qc !== "boolean" ||
    typeof item.eligible_for_final_media_qc_review !== "boolean" ||
    (item.superseded_reason !== null &&
      typeof item.superseded_reason !== "string")
  ) {
    throw new ProductionContractError();
  }
  for (const key of [
    "artifact_id",
    "artifact_revision_id",
    "run_id",
    "render_package_id",
    "render_package_revision_id",
    "job_id",
    "created_at",
  ]) {
    text(item[key]);
  }
  hash(item.render_package_sha256);
  hash(item.mp4_sha256);
  integer(item.byte_length, 1);
  const metadata = record(item.metadata);
  if (
    metadata.schema_version !== 1 ||
    metadata.video_stream_count !== 1 ||
    metadata.audio_stream_count !== 1
  ) {
    throw new ProductionContractError();
  }
  integer(metadata.duration_ms, 1);
  integer(metadata.width, 1);
  integer(metadata.height, 1);
  text(metadata.video_codec);
  text(metadata.audio_codec);
  return detached(item) as unknown as RenderArtifactV1;
}

export function validateRendererStageAccess(
  value: unknown,
): RendererStageAccessV1 {
  assertNoForbidden(value);
  const item = record(value);
  locks(item);
  if (
    item.schema_version !== 1 ||
    item.process_local !== true ||
    typeof item.restart_warning !== "string"
  ) {
    throw new ProductionContractError();
  }
  text(item.run_id);
  stringArray(item.blocker_codes);
  for (const key of [
    "renderer_stage_review_capability",
    "renderer_stage_approval_capability",
    "render_package_creation_capability",
    "bounded_render_execution_capability",
    "render_job_creation_capability",
    "render_job_cancellation_capability",
    "mp4_artifact_creation_capability",
    "render_artifact_review_capability",
    "eligible_for_final_media_qc_review",
  ]) {
    boolean(item[key]);
  }
  return detached({
    ...item,
    input_authority:
      item.input_authority === null
        ? null
        : validateRenderInputAuthority(item.input_authority),
    renderer_configuration: validateRendererConfiguration(
      item.renderer_configuration,
    ),
    output_profile: validateRendererOutputProfile(item.output_profile),
    approval: item.approval === null ? null : validateApproval(item.approval),
    render_package:
      item.render_package === null ? null : validatePackage(item.render_package),
    job: item.job === null ? null : validateRenderJob(item.job),
    artifact:
      item.artifact === null ? null : validateRenderArtifact(item.artifact),
  }) as unknown as RendererStageAccessV1;
}

export function validateRendererStageOperation(
  value: unknown,
): RendererStageOperationResultV1 {
  assertNoForbidden(value);
  const item = record(value);
  exactKeys(item, [
    "schema_version",
    "access",
    "approval",
    "render_package",
    "job",
    "artifact",
    "error",
  ]);
  if (item.schema_version !== 1) throw new ProductionContractError();
  const error = item.error === null ? null : record(item.error);
  if (error !== null) {
    exactKeys(error, ["schema_version", "code", "message", "retryable"]);
    if (
      error.schema_version !== 1 ||
      typeof error.code !== "string" ||
      typeof error.message !== "string" ||
      typeof error.retryable !== "boolean"
    ) {
      throw new ProductionContractError();
    }
  }
  return detached({
    schema_version: 1,
    access:
      item.access === null ? null : validateRendererStageAccess(item.access),
    approval: item.approval === null ? null : validateApproval(item.approval),
    render_package:
      item.render_package === null ? null : validatePackage(item.render_package),
    job: item.job === null ? null : validateRenderJob(item.job),
    artifact:
      item.artifact === null ? null : validateRenderArtifact(item.artifact),
    error,
  }) as RendererStageOperationResultV1;
}

export function validateRendererStageHistory(
  value: unknown,
): RendererStageHistoryV1 {
  assertNoForbidden(value);
  const item = record(value);
  exactKeys(item, [
    "schema_version",
    "run_id",
    "approvals",
    "render_packages",
    "jobs",
    "artifacts",
    "reviews",
    "process_local",
  ]);
  if (
    item.schema_version !== 1 ||
    item.process_local !== true ||
    !Array.isArray(item.approvals) ||
    !Array.isArray(item.render_packages) ||
    !Array.isArray(item.jobs) ||
    !Array.isArray(item.artifacts) ||
    !Array.isArray(item.reviews)
  ) {
    throw new ProductionContractError();
  }
  text(item.run_id);
  const reviews = item.reviews.map((value) => {
    const review = record(value);
    exactKeys(review, [
      "schema_version",
      "review_id",
      "artifact_revision_id",
      "action",
      "purpose",
      "reason",
      "created_at",
    ]);
    if (
      review.schema_version !== 1 ||
      !["GENERATED", "ACCEPTED_FOR_QC", "REJECTED", "RERENDER_REQUESTED"].includes(
        String(review.action),
      ) ||
      (review.purpose !== null &&
        review.purpose !== "SEPARATE_FINAL_MEDIA_QC_REVIEW") ||
      (review.reason !== null && typeof review.reason !== "string")
    ) {
      throw new ProductionContractError();
    }
    text(review.review_id);
    text(review.artifact_revision_id);
    text(review.created_at);
    return detached(review);
  });
  return detached({
    schema_version: 1,
    run_id: item.run_id,
    approvals: item.approvals.map(validateApproval),
    render_packages: item.render_packages.map(validatePackage),
    jobs: item.jobs.map(validateRenderJob),
    artifacts: item.artifacts.map(validateRenderArtifact),
    reviews,
    process_local: true,
  }) as unknown as RendererStageHistoryV1;
}
